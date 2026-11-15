/**
 * The capture pipeline: locate, read, project, map, deliver, commit.
 *
 * One call per hook boundary, for one native session, under the session lock.
 * The order inside it is the design's, and each step exists because skipping it
 * produces a specific bug:
 *
 *   1. replay what is already queued   — a newer turn must not overtake an older
 *                                        queued one, and a queued message is
 *                                        not a delivered one
 *   2. read every agent's wire         — main and subagents have separate
 *                                        cursors and separate OpenViking sessions
 *   3. deliver the new complete turns  — confirmed prefixes only
 *   4. commit                          — and only if nothing is outstanding
 *
 * The budget is passed in, and every network call is given what is left of it.
 * A hook that runs out of time keeps its cursors and tries again at the next
 * boundary; it never treats a timeout as a finished commit.
 */

import { stat } from "node:fs/promises";
import { join } from "node:path";

import {
  addAgentMessages,
  commitAgentSession,
  enqueueAgentPending,
  getAgentSession,
  replayAgentPendingDetailed,
} from "./shared/agent-hook-runtime.mjs";
import { isCaptureEnabled } from "./shared/capture-utils.mjs";
import { isRetryableFailure } from "./shared/retryable.mjs";
import { deriveHarnessSessionId } from "./shared/session-model.mjs";
import { listPending } from "./shared/pending-queue.mjs";
import { SESSION_PREFIX } from "./config.mjs";
import { mapTurnToMessages } from "./kimi-messages.mjs";
import {
  agentParentId,
  agentProfileName,
  listSessionAgents,
  locateSession,
  sessionEnvironmentId,
  sessionForkedFrom,
} from "./session-locator.mjs";
import {
  alreadyDelivered,
  captureFailureBlocks,
  captureFailureRecord,
  classifyReplayResult,
  commitSettledBy,
  deliveryOutcome,
  emptyAgentState,
  emptySessionState,
  evaluateCommitGuard,
  pendingForSession,
  pendingLostRecord,
  rememberStableIds,
} from "./session-state.mjs";
import {
  TURN_COMPLETE,
  TURN_RECOVERY,
  TURN_TERMINAL,
  addTurn,
  clearLedger,
  emptyLedger,
  messageRange,
  settleMessage,
  summarizeLedger,
} from "./turn-ledger.mjs";
import { emptyReaderState, readWireRecords } from "./wire-reader.mjs";
import { projectWire, turnHasContent } from "./wire-projector.mjs";

/** What the capture stage needs from a hook entry. */
export function captureContext({ cfg, input, cwd, log, logError, deadline, fetchJSON, peerId }) {
  return { cfg, input, cwd, log, logError, deadline, fetchJSON, peerId };
}

function remaining(deadline) {
  return Math.max(0, deadline - Date.now());
}

/** The OpenViking session id for one agent of one native session. */
export function ovSessionIdFor(nativeSessionId, agentId) {
  return agentId === "main"
    ? deriveHarnessSessionId(SESSION_PREFIX, nativeSessionId)
    : deriveHarnessSessionId(SESSION_PREFIX, nativeSessionId, `subagent-${agentId}`);
}

function agentMetaFromState(state, agentId) {
  return state?.agents?.[agentId];
}

/**
 * The earliest line this agent still has to re-read.
 *
 * A projection needs the open turn's opening records, so the window starts at
 * the open turn when there is one, and at the delivery cursor otherwise. The
 * reader returns absolute line numbers, so this stays a line, not an offset.
 */
function rereadLine(agent) {
  if (Number.isFinite(agent.openTurnLine)) return agent.openTurnLine;
  if (Number.isFinite(agent.ackLine) && agent.ackLine > 0) return agent.ackLine + 1;
  return 1;
}

/**
 * The byte offset that belongs to `line`, or `null` when it is not recorded.
 *
 * Line numbers and byte offsets have to agree: reading from byte zero while
 * numbering from line N would label every record with the wrong line, and the
 * branch tree is built from those numbers. So a cursor whose offset is unknown
 * re-reads the file from the start with line 1 — bounded, and the stable ids
 * keep the overlap from being re-sent.
 */
function rereadOffset(agent, line) {
  if (Number.isFinite(agent.openTurnLine) && line === agent.openTurnLine) {
    return Number.isFinite(agent.pendingTurnOffset) ? agent.pendingTurnOffset : null;
  }
  if (Number.isFinite(agent.ackLine) && line === agent.ackLine + 1) {
    return Number.isFinite(agent.ackOffset) ? agent.ackOffset : null;
  }
  return null;
}

/**
 * The byte offset of one record line, as this scan saw it.
 *
 * The open turn has to be re-read from its own first record, not from wherever
 * this scan's window began: a window that starts earlier would re-read turns
 * already delivered, and one that starts later would lose the turn's opening.
 */
/**
 * The byte offset just past `line`, as this scan saw it.
 *
 * A line this scan did not read keeps the cursor the caller already had, which
 * is the conservative answer: re-reading a little is safe, skipping is not.
 */
function offsetAfterLine(reader, line, fallback) {
  if (!Number.isFinite(line) || line <= 0) return 0;
  const entry = (reader?.records || []).find((item) => item.line === line);
  if (entry) return entry.endOffset;
  // A blank line produces no record, so the next record's start is the answer.
  const next = (reader?.records || []).find((item) => item.line === line + 1);
  return next ? next.offset : fallback;
}

function recordOffset(reader, line) {
  const entry = (reader?.records || []).find((item) => item.line === line);
  return entry?.offset;
}

function readerStateFrom(agent, wirePath) {
  return {
    ...emptyReaderState({ wirePath }),
    wirePath,
    scanOffset: agent.scanOffset || 0,
    line: agent.scanLine || 0,
    protocolVersion: agent.protocolVersion,
    minProtocolVersion: agent.minProtocolVersion,
    fileIdentity: agent.fileIdentity,
    prefixHash: agent.prefixHash,
    // The fingerprint window has to survive the process. Without it every read
    // re-derives the length from the file's current size, so a small wire that
    // grew by one turn looks like a replaced file and resets the cursor.
    prefixLength: agent.prefixLength,
  };
}

function persistReaderState(agent, reader) {
  return {
    ...agent,
    scanOffset: reader.scanOffset,
    scanLine: reader.line,
    protocolVersion: reader.protocolVersion ?? agent.protocolVersion,
    minProtocolVersion: reader.minProtocolVersion ?? agent.minProtocolVersion,
    fileIdentity: reader.fileIdentity ?? agent.fileIdentity,
    prefixHash: reader.prefixHash ?? agent.prefixHash,
    prefixLength: reader.prefixLength ?? agent.prefixLength,
    legacyWire: reader.legacy === true || agent.legacyWire === true,
    incompatibleWire: reader.incompatible === true || agent.incompatibleWire === true,
  };
}

/**
 * Turn one projection into the messages to deliver, respecting what is already
 * delivered and what the host's visibility rules allow.
 *
 * `kind` decides whether a turn may be sent at all:
 *   complete  yes
 *   terminal  yes, as a partial turn (failed/cancelled ended it)
 *   recovery  yes, as a partial turn, only from the ended sweep
 *   open      no
 */
function planTurn(turn, agent, cfg, { mode }) {
  const messages = [];
  const stableIds = [];
  const dropped = [];
  const segments = turn.segments || [];

  const mapped = mapTurnToMessages(turn, cfg, {
    ovSessionId: agent.ovSessionId,
  });
  for (let index = 0; index < mapped.payloads.length; index += 1) {
    const id = mapped.stableIds[index];
    if (alreadyDelivered(agent.stableIds, id)) continue;
    messages.push(mapped.payloads[index]);
    stableIds.push(id);
  }
  dropped.push(...mapped.dropped);

  return { messages, stableIds, dropped, mode, segments };
}

/**
 * One capture pass over one native session.
 *
 * Returns the state to persist plus a summary of what happened, so the caller
 * can decide about the ended marker without re-reading anything.
 */
export async function captureSession({
  cfg,
  input,
  cwd,
  log,
  logError,
  deadline,
  fetchJSON,
  peerId = "",
  sessionId,
  state,
  mode = "normal",
  now = Date.now(),
  // A caller that expects a long backlog (backfill) may bound each pass. The
  // reader stops at the last complete line inside the window, and the next
  // pass re-reads from the first unacknowledged turn, so a window only limits
  // how much work one pass commits to before its state is persisted.
  scanLimitBytes = undefined,
}) {
  const summary = {
    status: "ok",
    agents: {},
    delivered: 0,
    queued: 0,
    committed: 0,
    blocked: [],
    replayed: 0,
    pendingLost: 0,
  };

  const located = await locateSession(sessionId, { home: cfg.kimiCodeHome || undefined });
  if (located.status !== "ok") {
    summary.status = located.status;
    summary.error = located.error || located.reason;
    if (located.status === "unreadable") {
      // Keep every cursor. Resetting here would replay the whole transcript.
      return { state, summary };
    }
    return { state, summary };
  }

  if (sessionEnvironmentId(located.state) !== "local") {
    // The wire for a remote environment is not on this machine; guessing at a
    // path would capture someone else's file. Persist the reason, because a
    // skip that lives only in this process's summary is indistinguishable from
    // a session that silently never captured once the hook exits.
    const environmentId = sessionEnvironmentId(located.state);
    summary.status = "non_local_environment";
    summary.environmentId = environmentId;
    return {
      state: {
        ...(state || {}),
        nativeSessionId: sessionId,
        diagnostics: {
          ...((state || {}).diagnostics || {}),
          nonLocalEnvironment: { environmentId, at: now },
        },
      },
      summary,
    };
  }

  const { agents: discovered, agentsRoot } = await listSessionAgents(located.sessionDir, located.state);
  if (discovered.length === 0) {
    summary.status = "no_agents";
    return { state, summary };
  }

  const forkedFrom = sessionForkedFrom(located.state);
  let nextState = {
    ...emptySessionState(),
    ...state,
    nativeSessionId: sessionId,
    agents: { ...(state?.agents || {}) },
    forkedFrom: forkedFrom || state?.forkedFrom,
    sessionDir: located.sessionDir,
    // Where this session actually ran. The sweep may be started by another
    // project's hook, and this is the only correct context for resolving the
    // session's own configuration and peer later.
    // The session's own directory is authoritative: `state.json` records where
    // it ran, and the payload's cwd is whatever project invoked this hook. The
    // sweep later needs the former, because it is started from a different one.
    workspaceCwd: String(located.state?.cwd || cwd || located.workDir || state?.workspaceCwd || ""),
    // The parent session and the subagent's own labels are diagnostics: they
    // explain a fork and a subagent relationship without inventing a field the
    // server does not have.
    parentSessionId: forkedFrom || state?.parentSessionId,
  };

  // This session is reachable here now, so a previous remote-environment skip
  // no longer describes it.
  if (nextState.diagnostics?.nonLocalEnvironment) {
    const { nonLocalEnvironment, ...diagnostics } = nextState.diagnostics;
    nextState = { ...nextState, diagnostics };
  }

  // Every discovered agent gets its state record before anything else happens,
  // because the replay below has to match queued operations to an agent by its
  // OpenViking session id. A first run has no records yet, and without this the
  // queue of a session this plugin already wrote to would be invisible.
  for (const agentInfo of discovered) {
    if (nextState.agents[agentInfo.id]) continue;
    nextState = {
      ...nextState,
      agents: {
        ...nextState.agents,
        [agentInfo.id]: {
          ...emptyAgentState({
            agentId: agentInfo.id,
            ovSessionId: ovSessionIdFor(sessionId, agentInfo.id),
          }),
          fresh: true,
        },
      },
    };
  }

  // Replay before new work, so a queued message is not overtaken by a newer
  // one and a queued commit is settled before another is attempted. It runs
  // even when capture is off: queued messages are history the user already
  // produced, and `autoCapture` is a switch about new capture, not about
  // abandoning what was already promised.
  const replay = await replayForSession({
    cfg,
    fetchJSON,
    log,
    sessionId,
    state: nextState,
    deadline,
    mode,
    now,
  });
  nextState = replay.state;
  summary.replayed = replay.replayed;
  summary.pendingLost += replay.pendingLost;
  if (replay.blocked) summary.blocked.push("replay_incomplete");

  if (!isCaptureEnabled(cfg)) {
    // New capture and the commit it would trigger are off; the replay above
    // has already done its part.
    summary.status = "capture_disabled";
    return { state: nextState, summary };
  }

  for (const agentInfo of discovered) {
    if (remaining(deadline) <= 0) {
      summary.status = "budget_exhausted";
      summary.blocked.push("budget");
      break;
    }
    const result = await captureAgent({
      cfg,
      agentInfo,
      agentsRoot,
      sessionDir: located.sessionDir,
      nativeSessionId: sessionId,
      state: nextState,
      deadline,
      fetchJSON,
      peerId,
      log,
      logError,
      mode,
      now,
      scanLimitBytes,
    });
    nextState = result.state;
    summary.agents[agentInfo.id] = result.summary;
    summary.delivered += result.summary.delivered;
    summary.queued += result.summary.queued;
    summary.committed += result.summary.committed;
    if (result.summary.blocked) summary.blocked.push(...result.summary.blocked.map((r) => `${agentInfo.id}:${r}`));
    // An agent-level verdict that is not "ok" is the session's verdict too: a
    // fork without its marker, an unreadable wire or a blocked capture is not
    // something the caller should have to dig out of the per-agent records.
    if (result.summary.status && result.summary.status !== "ok" && summary.status === "ok") {
      summary.status = result.summary.status;
      if (result.summary.error) summary.error = result.summary.error;
    }
  }

  return { state: nextState, summary };
}

/**
 * Replay this session's queued operations and fold the per-operation results
 * back into the agents' state.
 *
 * The predicate is what makes `resume` safe: a commit queued before the user
 * resumed is held back until the resumed generation has delivered a complete
 * turn of its own, so an old ended marker cannot seal a session that is now
 * running.
 */
async function replayForSession({ cfg, fetchJSON, log, sessionId, state, deadline, mode, now }) {
  const ovIds = new Set(Object.values(state.agents || {}).map((agent) => agent.ovSessionId).filter(Boolean));
  if (ovIds.size === 0) return { state, replayed: 0, pendingLost: 0 };

  const budget = remaining(deadline);
  if (budget <= 0) return { state, replayed: 0, pendingLost: 0, blocked: true };

  const result = await replayAgentPendingDetailed(fetchJSON, log, {
    shouldReplay: (entry) => {
      if (!ovIds.has(entry.sessionId)) return false;
      if (entry.type !== "commitSession") return true;
      const agent = Object.values(state.agents).find((item) => item.ovSessionId === entry.sessionId);
      if (!agent) return false;
      // A resumed generation holds its predecessor's commit until it has
      // delivered something new; otherwise the first event after a resume
      // would archive a session the user just reopened.
      if (agent.resumeBlockedCommit) return false;
      return true;
    },
  });

  let next = state;
  let pendingLost = 0;
  for (const item of result.results || []) {
    const agentId = Object.keys(next.agents).find((id) => next.agents[id].ovSessionId === item.sessionId);
    if (!agentId) continue;
    const agent = next.agents[agentId];
    const verdict = classifyReplayResult(item, agent);

    if (verdict.effect === "message_delivered") {
      // A replayed message is delivered now. Settle it in the ledger by the
      // queue's dedup key, which is the only identity a replay reports.
      const ledger = settleMessage(agent.ledger, { dedupKey: item.dedupKey });
      next = {
        ...next,
        agents: {
          ...next.agents,
          [agentId]: {
            ...agent,
            ledger,
            // A replayed message is not by itself a reason to release the
            // resume hold: it is usually the *previous* generation's backlog,
            // and letting it through would archive the session the user just
            // reopened. The hold lifts only when this generation's own complete
            // turn is fully delivered, which `releaseResumeHold` decides.
            lastDeliveredAt: now,
          },
        },
      };
      continue;
    }

    if (verdict.effect === "commit_delivered") {
      if (commitSettledBy(item, agent.commitPending)) {
        next = {
          ...next,
          agents: {
            ...next.agents,
            [agentId]: {
              ...agent,
              commitPending: undefined,
              ledger: clearLedger(agent.ledger),
              commitTokenCount: undefined,
              resumeBlockedCommit: false,
              committedAt: now,
            },
          },
        };
      }
      continue;
    }

    if (verdict.effect === "message_lost" || verdict.effect === "commit_lost") {
      pendingLost += 1;
      // The range belongs to the message that was dropped, not to the whole
      // ledger: a commit has no wire range at all, and a message's range is its
      // own turn's. Claiming otherwise makes the record unusable for deciding
      // what to accept.
      const range = verdict.effect === "message_lost"
        ? messageRange(agent.ledger, { dedupKey: item.dedupKey })
        : undefined;
      const record = pendingLostRecord({
        agentId,
        ovSessionId: agent.ovSessionId,
        dedupKey: item.dedupKey,
        type: item.type,
        errorKind: verdict.errorKind || "queue_dropped",
        wireStart: range?.wireStart,
        wireEnd: range?.wireEnd,
        stableIds: range?.stableIds,
        at: now,
      });
      next = {
        ...next,
        agents: {
          ...next.agents,
          [agentId]: {
            ...agent,
            pendingLost: [...(agent.pendingLost || []), record],
            hasCaptureGaps: true,
          },
        },
      };
    }
  }
  return { state: next, replayed: result.replayed || 0, pendingLost, blocked: (result.failed || 0) > 0 };
}

/**
 * Capture one agent: read, project, deliver, maybe commit.
 */
async function captureAgent({
  cfg,
  agentInfo,
  sessionDir,
  nativeSessionId,
  state,
  deadline,
  fetchJSON,
  peerId,
  log,
  logError,
  mode,
  now,
  scanLimitBytes,
}) {
  const agentId = agentInfo.id;
  const ovSessionId = ovSessionIdFor(nativeSessionId, agentId);
  const parentAgentId = agentParentId(agentInfo, state);
  const existing = agentMetaFromState(state, agentId);
  let agent = existing || emptyAgentState({ agentId, ovSessionId });
  agent = {
    ...agent,
    agentId,
    ovSessionId,
    // Local diagnostics only: the server has no parent field, and inventing one
    // would make two hosts disagree about what a subagent is.
    parentAgentId: parentAgentId || agent.parentAgentId,
    profileName: agentProfileName(agentInfo) || agent.profileName,
  };
  const summary = {
    agentId,
    status: "ok",
    delivered: 0,
    queued: 0,
    committed: 0,
    turns: 0,
    open: 0,
    blocked: [],
  };

  if (agentInfo.error === "homedir_outside_session") {
    summary.status = "homedir_outside_session";
    summary.blocked.push("homedir_outside_session");
    return { state: { ...state, agents: { ...state.agents, [agentId]: agent } }, summary };
  }

  const wirePath = join(agentInfo.homedir, "wire.jsonl");
  if (agent.wirePath && agent.wirePath !== wirePath) {
    // The agent's home moved: a new file with a new cursor. Starting over is
    // right here, and the stable ids keep the overlap from being re-sent.
    agent = { ...agent, wirePath, scanOffset: 0, scanLine: 0, ackOffset: 0, ackLine: 0 };
  }
  agent.wirePath = wirePath;

  // ---- take-over / fork boundaries ----------------------------------------
  // "First run" cannot be `!existing`: the session-level pass creates an empty
  // record for every discovered agent so the replay can match queued operations
  // to it. The marker is explicit instead, and it is consumed once so a later
  // pass does not re-apply the boundary over real cursors.
  if (agent.fresh === true) {
    agent = applyInitialBoundary(agent, {
      cfg,
      state,
      mode,
      wirePath,
      nativeSessionId,
    });
    agent = { ...agent, fresh: false };
  }

  // ---- read ---------------------------------------------------------------
  // A first sighting that is not a brand-new session starts at the current end
  // of the file: the plugin was installed mid-session, or a corrupt state lost
  // its cursors, and replaying the whole wire as new memory is exactly what the
  // design forbids. A fork starts at its `forked` marker instead, which is
  // found by reading the file once and then never again.
  let startLine = Math.max(1, rereadLine(agent));
  let startOffset = rereadOffset(agent, startLine);
  if (startOffset === null) {
    // No recorded offset for that line: re-read from the beginning so the
    // numbering and the bytes stay in step. The two always move together.
    startLine = 1;
    startOffset = 0;
  }
  if (agent.startAtEof) {
    const scanned = await readWireRecords(wirePath, readerStateFrom(agent, wirePath), { limitBytes: scanLimitBytes });
    if (scanned.status === "ok") {
      startLine = scanned.state.line + 1;
      startOffset = scanned.state.scanOffset;
      agent = {
        ...persistReaderState(agent, scanned.state),
        startAtEof: false,
        initialBoundary: agent.initialBoundary,
      };
    } else if (scanned.status !== "missing") {
      // An incompatible or legacy file still tells us how far it goes; keep the
      // verdict and stop rather than start reading records we cannot interpret.
      agent = persistReaderState(agent, scanned.state);
      summary.status = scanned.status === "legacy" ? "legacy_wire" : "incompatible_wire";
      return { state: { ...state, agents: { ...state.agents, [agentId]: agent } }, summary };
    }
  }
  const reader = await readWireRecords(wirePath, readerStateFrom(agent, wirePath), {
    startLine,
    startOffset,
    limitBytes: scanLimitBytes,
  });

  if (reader.status === "missing") {
    summary.status = "missing_wire";
    return { state: { ...state, agents: { ...state.agents, [agentId]: agent } }, summary };
  }
  if (reader.status === "unreadable") {
    summary.status = "unreadable_wire";
    summary.error = reader.error;
    agent.lastError = { at: now, kind: "wire_unreadable", detail: reader.error };
    return { state: { ...state, agents: { ...state.agents, [agentId]: agent } }, summary };
  }
  if (reader.status === "legacy") {
    summary.status = "legacy_wire";
    agent = { ...persistReaderState(agent, reader.state), legacyWire: true };
    return { state: { ...state, agents: { ...state.agents, [agentId]: agent } }, summary };
  }
  if (reader.status === "incompatible") {
    summary.status = "incompatible_wire";
    summary.minProtocolVersion = reader.minProtocolVersion;
    agent = {
      ...persistReaderState(agent, reader.state),
      incompatibleWire: true,
      lastError: { at: now, kind: "protocol_incompatible", min: reader.minProtocolVersion },
    };
    return { state: { ...state, agents: { ...state.agents, [agentId]: agent } }, summary };
  }

  agent = persistReaderState(agent, reader.state);
  const forkDecision = applyForkBoundary(agent, { records: reader.records, lastLine: reader.state.line, now });
  agent = forkDecision.agent;
  if (forkDecision.markerMissing) {
    // The marker was missing: this read only located the end of the file, and
    // nothing in it may be imported.
    summary.status = "fork_without_marker";
    return { state: { ...state, agents: { ...state.agents, [agentId]: agent } }, summary };
  }
  if (reader.reset) {
    // The file was replaced or truncated. That is not a gap on its own — the
    // stable ids suppress the overlap — but a reset does mean this read cannot
    // prove the previous prefix was delivered, so it is recorded as a
    // diagnostic and left for `ackLine` to decide. Marking `hasCaptureGaps`
    // here would block every commit after any repair, which is the opposite of
    // what the flag is for: it means data the server will never get.
    // The line numbers the carried edges refer to belong to the old file. Keeping
    // them would let an old switch hide a real one on the same line in the new
    // file, and would place records in a branch that no longer exists.
    agent = {
      ...agent,
      resetAt: now,
      resets: (Number(agent.resets) || 0) + 1,
      branchEdges: [],
      activeBranch: "main",
    };
  }

  // A permanently failed capture is not retried on every event. The record
  // carries the fingerprint of the configuration that produced it; the same
  // configuration means the same failure, so this pass reads nothing, sends
  // nothing and keeps the cursor exactly where the failure left it. A changed
  // configuration lifts the record here and the range below is rebuilt from its
  // recorded start, which is why the start is part of the record.
  // A failed *commit* only blocks committing. The messages themselves are
  // fine, and stopping capture for it would leave a growing hole on the wire
  // until someone ran `recover.mjs`. Only a failed *delivery* stops reading.
  if (captureFailureBlocks(agent, cfg) && agent.captureFailed?.kind !== "commit_failed") {
    summary.status = "capture_blocked";
    summary.blocked.push("capture_failed");
    return { state: { ...state, agents: { ...state.agents, [agentId]: agent } }, summary };
  }
  if (agent.captureFailed && !agent.captureFailed.resolved && agent.captureFailed.kind === "non_retryable_delivery") {
    // The configuration changed. Re-read from the range's start rather than
    // from where the scan got to: the scan cursor had already moved past the
    // turn, and resuming there would silently skip exactly the messages that
    // failed. The range is the authority for this one pass.
    const failed = agent.captureFailed;
    agent = { ...agent, captureFailed: { ...failed, resolved: true, resolvedAt: now } };
    if (Number.isFinite(failed.wireStart)) {
      agent = {
        ...agent,
        ackLine: Math.min(agent.ackLine || 0, failed.wireStart),
        scanLine: Math.min(agent.scanLine || 0, failed.wireStart),
        scanOffset: Math.min(agent.scanOffset || 0, Number.isFinite(failed.wireStartOffset) ? failed.wireStartOffset : agent.scanOffset || 0),
      };
    }
  }

  // ---- project ------------------------------------------------------------
  const projection = projectWire(reader.records, {
    lastLine: reader.state.line,
    // The switch that owns this window's branch may be in an earlier event.
    carriedEdges: agent.branchEdges || [],
  });
  if (projection.status !== "ok") {
    summary.status = "projection_failed";
    summary.error = projection.error;
    agent.lastError = { at: now, kind: "projection_failed", detail: projection.error };
    return { state: { ...state, agents: { ...state.agents, [agentId]: agent } }, summary };
  }

  // The branch geometry outlives this scan's window: the next event may start
  // after the switch that put this agent on its current branch.
  agent = {
    ...agent,
    branchEdges: projection.edges,
    activeBranch: projection.activeBranch,
  };

  // A fork's inherited prefix is not this session's new memory. The `forked`
  // marker in the wire is the boundary: everything at or before its line was
  // copied from the parent, and the first complete new turn starts after it.
  const forkedLine = findForkedLine(reader.records);
  if (forkedLine !== undefined) {
    agent = { ...agent, forkBoundaryLine: forkedLine };
  }
  const boundary = agent.forkBoundaryLine;
  const turns = boundary === undefined
    ? projection.turns
    : projection.turns.filter((turn) => (turn.endLine ?? turn.startLine) > boundary);

  const plan = { messages: [], stableIds: [] };
  let lastSettledLine = agent.ackLine || 0;
  let openTurnLine;
  let openTurnOffset;
  let lastOpenTurn;
  // The ended sweep may close an open turn, but only when it is the session's
  // own ending that makes it closable — the marker is read from the session
  // state, not from the agent record, so it is passed down here.
  const endedMarkerSeen = state.endedMarkerSeen === true || Boolean(state.endedMarker);

  for (const turn of turns) {
    const sendable = turn.complete
      || turn.terminal
      || (mode === "recovery" && turn.open && endedMarkerSeen);
    if (!sendable) {
      if (turn.open) {
        // An open turn is only a reason to re-read when it is the *last* turn on
        // the active chain. A turn the branch abandoned is still "open" in the
        // file — its `turn.ended` lives on a branch that is no longer active —
        // and treating it as the open tail would stall every later turn.
        openTurnLine = turn.startLine;
        openTurnOffset = recordOffset(reader, turn.startLine);
        summary.open += 1;
        lastOpenTurn = turn;
      }
      continue;
    }
    if (!turnHasContent(turn)) {
      lastSettledLine = Math.max(lastSettledLine, turn.endLine ?? turn.startLine);
      continue;
    }
    const mapped = planTurn(turn, agent, cfg, { mode });
    if (mapped.messages.length > 0) {
      plan.messages.push({ turn, ...mapped });
      for (const id of mapped.stableIds) plan.stableIds.push(id);
    }
    lastSettledLine = Math.max(lastSettledLine, turn.endLine ?? turn.startLine);
    summary.turns += 1;
  }

  // ---- queue guard ---------------------------------------------------------
  // New messages must not overtake older ones still waiting in the queue for
  // this same OpenViking session: the server would receive turn N+1 before
  // turn N, and the transcript would be out of order forever. The replay above
  // already tried; anything still queued stops new sends for this agent only.
  const queuedNow = pendingForSession(await listPending(), ovSessionId);
  const blockedByQueue = queuedNow.messages.length > 0;
  if (blockedByQueue) {
    summary.blocked.push("pending_add_messages");
    summary.queuedWaiting = queuedNow.messages.length;
  }

  // ---- deliver ------------------------------------------------------------
  let settledLine = agent.ackLine || 0;
  let deliveredThisPass = false;
  let ledger = agent.ledger || emptyLedger();

  if (plan.messages.length > 0 && remaining(deadline) > 0 && !blockedByQueue) {
    const payloads = plan.messages.flatMap((item) => item.messages);
    const stableIds = plan.messages.flatMap((item) => item.stableIds);
    const result = await addAgentMessages(fetchJSON, ovSessionId, payloads, peerId);
    const outcome = deliveryOutcome(result, payloads.length);
    summary.delivered = outcome.sent;
    summary.queued = outcome.queued;

    // Only an explicit confirmation is recorded. The shared sender reports one
    // item per payload: `sent`, `queued` with the queue's dedup key, `failed`,
    // or `enqueue_failed`. A missing item is not evidence of anything, and
    // inventing `queued` for it would make the ledger claim durable handover
    // that never happened.
    const items = Array.isArray(result?.items) ? result.items : [];
    const confirmedStatus = (index) => {
      const item = items.find((candidate) => candidate.index === index);
      if (item?.status === "sent") return { id: stableIds[index], status: "sent" };
      if (item?.status === "queued" && item.dedupKey) {
        return { id: stableIds[index], status: "queued", dedupKey: item.dedupKey };
      }
      return undefined;
    };

    // The handover cursor is the contiguous prefix of payloads that were either
    // sent or durably queued. It is deliberately independent of whether a whole
    // turn is complete: a queued message is durable, the design allows the
    // cursor to pass it, and the *delivery* state of that turn stays `queued`
    // in the ledger. Stopping at the first unconfirmed payload is what keeps a
    // later turn's line from hiding an earlier hole.
    let cursor = 0;
    let contiguousConfirmed = 0;
    let lastConfirmedLine = settledLine;
    for (const item of plan.messages) {
      const count = item.messages.length;
      // Keep one slot per message so a gap in the middle does not shift the
      // confirmed ones onto the wrong ids.
      const slots = [];
      for (let i = 0; i < count; i += 1) slots.push(confirmedStatus(cursor + i));
      const statuses = slots.filter(Boolean);
      const allConfirmed = slots.every(Boolean);
      if (allConfirmed) {
        // Every message of this turn was handed over, in order, with no gap.
        ledger = addTurn(ledger, {
          turnId: turnLedgerId(item.turn),
          kind: turnKindOf(item.turn, mode),
          wireStart: item.turn.startLine,
          wireEnd: item.turn.endLine ?? item.turn.startLine,
          messages: statuses,
          messageCount: count,
          at: now,
        });
        contiguousConfirmed += count;
        lastConfirmedLine = Math.max(lastConfirmedLine, item.turn.endLine ?? item.turn.startLine);
      } else {
        // A partial turn is recorded for the messages that were confirmed, but
        // the cursor stops here: the next turn must not overtake this hole.
        if (statuses.length > 0) {
          ledger = addTurn(ledger, {
            turnId: turnLedgerId(item.turn),
            kind: turnKindOf(item.turn, mode),
            wireStart: item.turn.startLine,
            wireEnd: item.turn.endLine ?? item.turn.startLine,
            messages: statuses,
            messageCount: count,
            at: now,
          });
        }
        break;
      }
      cursor += count;
    }

    // Every explicitly confirmed message is remembered, not just the contiguous
    // prefix. A batch that fails half way leaves the confirmed messages in the
    // ledger; if they were not also remembered as delivered, the next pass would
    // re-send them, and the server has no message id to deduplicate with. The
    // cursor stays contiguous — that is a separate promise.
    const confirmedIds = [];
    for (let index = 0; index < stableIds.length; index += 1) {
      if (confirmedStatus(index)) confirmedIds.push(stableIds[index]);
    }
    if (confirmedIds.length > 0) {
      agent = { ...agent, stableIds: rememberStableIds(agent.stableIds, confirmedIds) };
    }

    if (contiguousConfirmed > 0) {
      agent = {
        ...agent,
        lastDeliveredAt: outcome.sent > 0 ? now : agent.lastDeliveredAt,
        // Where this generation's own new turns begin. A resume hold is
        // released only by a complete turn delivered at or after this line.
        generationStartLine: agent.generationStartLine ?? plan.messages[0].turn.startLine,
      };
      deliveredThisPass = outcome.sent > 0;
      if (deliveredThisPass && agent.captureFailed?.resolved) {
        // The retry that followed a configuration change worked. Clearing the
        // record is what lets the commit proceed.
        agent = { ...agent, captureFailed: undefined };
      }
      settledLine = lastConfirmedLine;
      agent.ackLine = settledLine;
    }

    if (outcome.blocksCommit) {
      summary.blocked.push(outcome.nonRetryable ? "capture_failed" : "delivery_incomplete");
      if (outcome.nonRetryable) {
        agent = {
          ...agent,
          captureFailed: {
            ...captureFailureRecord({
              cfg,
              kind: "non_retryable_delivery",
              errorKind: result?.lastError?.code || result?.lastError?.message || "unknown",
              wireStart: agent.ackLine || 0,
              wireEnd: lastSettledLine,
              stableIds: stableIds.slice(0, 64),
              at: now,
            }),
            wireStartOffset: recordOffset(reader, (agent.ackLine || 0) + 1),
          },
        };
      }
    } else if (outcome.failed > 0) {
      summary.blocked.push("delivery_queued");
    }
  } else if (plan.messages.length > 0 && blockedByQueue) {
    // Nothing was sent because older messages are still queued. The new turns
    // stay unread past the cursor, so the next boundary rebuilds them.
    summary.status = "queued_ahead";
  } else if (plan.messages.length > 0) {
    // Out of budget before sending anything: nothing is recorded, so the next
    // pass rebuilds these turns instead of believing they were delivered.
    summary.status = "budget_exhausted";
    summary.blocked.push("budget");
  } else {
    settledLine = lastSettledLine;
  }

  agent = { ...agent, ledger };
  agent = releaseResumeHold(agent, { now });

  // Only the final turn on the chain can still be receiving records, and only
  // if it is still ahead of the delivery cursor. An earlier turn that never
  // ended was abandoned by a branch switch — its `turn.ended` lives on a branch
  // that is no longer active — and its records will never arrive here.
  const lastTurn = turns.length ? turns[turns.length - 1] : undefined;
  const openIsTail = openTurnLine !== undefined
    && lastOpenTurn === lastTurn
    && openTurnLine > (agent.ackLine || 0);
  if (openIsTail) {
    agent = {
      ...agent,
      openTurnLine,
      // The open turn's own byte start, taken from the record the reader
      // returned, not from wherever this scan's window happened to begin.
      pendingTurnOffset: openTurnOffset ?? agent.pendingTurnOffset,
    };
  } else {
    agent = { ...agent, openTurnLine: undefined };
    agent.ackLine = Math.max(agent.ackLine || 0, settledLine);
    // The byte cursor has to agree with the line cursor. Taking the scan's
    // offset here would claim bytes that were never acknowledged: after a
    // failed delivery the scan has moved past a turn the cursor is still
    // before, and the next read would resume beyond it.
    agent.ackOffset = offsetAfterLine(reader, agent.ackLine, agent.ackOffset || 0);
  }
  agent.pendingTurnLine = startLine;

  // ---- commit -------------------------------------------------------------
  // A recovery partial is a terminal turn produced by the ended sweep, and the
  // design lets it take part in a forced boundary: the session is over, so the
  // token and turn thresholds have no more events left to reach.
  const force = mode === "compact" || mode === "session-end" || mode === "recovery";
  if (cfg.autoCommitOnCompact === false && mode === "compact") {
    summary.blocked.push("auto_commit_on_compact_off");
  } else if (remaining(deadline) > 0) {
    const committed = await maybeCommit({
      cfg,
      fetchJSON,
      agent,
      ovSessionId,
      log,
      deadline,
      force,
      // Only an open *tail* may block a normal boundary. A turn the branch
      // abandoned is still open in the file but will never receive its
      // `turn.ended` here, and blocking on it would stall every later commit.
      // A forced boundary (PreCompact/SessionEnd) commits the stable prefix
      // even while the tail is still being written; the open tail's messages
      // are not in the ledger, so the archive cannot contain a half-turn.
      openTurn: openIsTail && !force,
      deliveredThisPass,
      mode,
      now,
      summary,
    });
    agent = committed.agent;
    summary.committed = committed.committed;
    if (committed.blocked) summary.blocked.push(...committed.blocked);
  }

  return { state: { ...state, agents: { ...state.agents, [agentId]: agent } }, summary };
}

/**
 * The line of the `forked` record, or undefined when the wire has none.
 *
 * A copied fork carries the parent's records and then one `forked` line; a
 * turn-sliced fork starts with it. Its line is the boundary between inherited
 * history and this session's own turns.
 */
function findForkedLine(records) {
  for (const entry of records || []) {
    if (entry?.record?.type === "forked") return entry.line;
  }
  return undefined;
}

/**
 * Where a fork's own history starts, once the file has been read.
 *
 * With a marker, everything up to its line is the parent's and is skipped. With
 * no marker at all, the fork's own content cannot be told apart from inherited
 * content, so capture starts at the end of the file and says so. Guessing would
 * re-import history the parent already has, which is the duplicate the design
 * exists to avoid.
 */
function applyForkBoundary(agent, { records, lastLine, now }) {
  if (agent.initialBoundary !== "fork" || agent.forkBoundaryResolved) {
    return { agent, markerMissing: false };
  }
  const marker = findForkedLine(records);
  if (marker !== undefined) {
    return { agent: { ...agent, forkBoundaryLine: marker, forkBoundaryResolved: true }, markerMissing: false };
  }
  return {
    agent: {
      ...agent,
      forkBoundaryResolved: true,
      forkBoundaryLine: lastLine,
      forkDiagnostic: { at: now, reason: "forked_marker_missing", atLine: lastLine },
    },
    markerMissing: true,
  };
}

/**
 * Release a resume hold, and only for the reason it exists.
 *
 * A resume writes `resumedAt` and holds the previous generation's queued
 * commit, so an old ended marker cannot seal a session the user just reopened.
 * The hold lifts when this generation has itself delivered a complete turn —
 * every message sent — that started at or after the generation boundary. A
 * replayed message from the backlog, a terminal partial, or half a turn are all
 * explicitly not enough: each of them can belong entirely to the previous
 * generation.
 */
function releaseResumeHold(agent, { now } = {}) {
  if (!agent?.resumeBlockedCommit) return agent;
  const boundary = Number.isFinite(agent.resumeBoundaryLine)
    ? agent.resumeBoundaryLine
    : (Number.isFinite(agent.generationStartLine) ? agent.generationStartLine : undefined);
  if (boundary === undefined) return agent;
  const released = (agent.ledger?.turns || []).some((turn) => (
    turn.kind === TURN_COMPLETE
    && Number.isFinite(turn.wireStart)
    && turn.wireStart >= boundary
    && turn.messageCount > 0
    && turn.messages.length >= turn.messageCount
    && turn.messages.every((message) => message.status === "sent")
  ));
  return released ? { ...agent, resumeBlockedCommit: false, resumeReleasedAt: now } : agent;
}

/** The ledger identity of one projected turn. */
function turnLedgerId(turn) {
  return turn.turnId !== undefined && turn.turnId !== null
    ? String(turn.turnId)
    : `line-${turn.startLine}`;
}

/**
 * Which boundary may commit this turn.
 *
 * A `completed` turn that every step sealed is complete; anything else that
 * ended is a terminal partial; an open turn closed by the ended sweep is a
 * recovery partial. The distinction is what keeps a normal `Stop` from
 * archiving a `failed`/`cancelled` turn.
 */
function turnKindOf(turn, mode) {
  if (turn.open) return mode === "recovery" ? TURN_RECOVERY : TURN_TERMINAL;
  if (turn.complete) return TURN_COMPLETE;
  return TURN_TERMINAL;
}

/**
 * Where capture starts for an agent this plugin has never seen.
 *
 * Three cases, and getting them wrong is the difference between "the plugin
 * starts remembering now" and "the plugin re-imports a year of history":
 *
 * - A brand-new session (`SessionStart` with source `startup`) starts at the
 *   beginning, because everything in it is new.
 * - Any other first sighting — a plugin installed mid-session, a resume, a fork
 *   without its marker — starts at the current end of the file.
 * - A fork with a `forked` marker starts at the first complete turn after it.
 */
function applyInitialBoundary(agent, { cfg, state, mode, wirePath, nativeSessionId }) {
  const source = state.startSource;
  const forked = Boolean(state.forkedFrom);
  // A quarantined state lost its cursors, so "startup" no longer proves the
  // session is new: the design says a corrupt state resumes from the file end,
  // never from line 1.
  const fromStart = source === "startup" && !forked && !state.takeover && !state.corruptRecovered;
  const next = { ...agent };
  if (fromStart) {
    next.scanOffset = 0;
    next.scanLine = 0;
    next.ackOffset = 0;
    next.ackLine = 0;
    next.initialBoundary = "start";
  } else if (forked) {
    // A fork reads from the start of the file to find its `forked` marker: the
    // marker is the boundary, and the inherited prefix before it is filtered
    // out. A fork whose marker never appears — a turn-sliced fork, or a copy
    // made before the host wrote one — must not fall back to importing the
    // prefix. `applyForkBoundary` decides that once the file has been read, so
    // this pass starts at byte zero and reads nothing into the ledger.
    next.initialBoundary = "fork";
    next.scanOffset = 0;
    next.scanLine = 0;
    next.ackOffset = 0;
    next.ackLine = 0;
  } else {
    next.initialBoundary = "eof";
    // The reader will skip to the end on its first pass; this flag tells it to.
    next.startAtEof = true;
  }
  if (state.takeover) next.takeoverAt = Date.now();
  return next;
}

/**
 * Decide whether to commit, and do it.
 *
 * The guard is evaluated before the network call, not after: a commit that
 * should not have happened cannot be taken back, and the cost of asking first
 * is one function call.
 */
async function maybeCommit({
  cfg,
  fetchJSON,
  agent,
  ovSessionId,
  log,
  deadline,
  force,
  openTurn,
  deliveredThisPass,
  mode,
  now,
  summary,
}) {
  const pending = await listPending();
  const forSession = pendingForSession(pending, ovSessionId);

  // The token threshold needs metadata, and a GET is only worth it when there
  // is something to commit: a message delivered in this pass, or — at a forced
  // boundary — anything delivered earlier that is still uncommitted. An idle
  // Stop must not add a GET, and a PreCompact that follows a Stop must, or a
  // short session whose tokens never crossed the threshold would never commit.
  const summaryLedger = summarizeLedger(agent.ledger);
  const hasCommittable = summaryLedger.completeSent + summaryLedger.terminalSent > 0;
  let tokenCount = agent.commitTokenCount;
  let metadataUnavailable = false;
  const shouldAsk = (deliveredThisPass === true || (force && hasCommittable)) && remaining(deadline) > 0;
  if (shouldAsk) {
    const session = await getAgentSession(fetchJSON, ovSessionId, { autoCreate: false });
    if (session === null) {
      // The design is explicit: `null` means the metadata could not be read,
      // and this boundary must not commit on a guess. It is not "zero tokens",
      // and the turn fallback is not a licence to commit either.
      metadataUnavailable = true;
      summary.blocked.push("metadata_unavailable");
      tokenCount = undefined;
    } else {
      const value = session?.pending_tokens ?? session?.pendingTokens;
      // A server that answered but omitted the field is the one case the
      // design allows the turn threshold to cover.
      tokenCount = typeof value === "number" && Number.isFinite(value) ? value : 0;
    }
  }
  const withTokens = { ...agent, commitTokenCount: tokenCount };

  if (metadataUnavailable) {
    return { agent: withTokens, committed: 0, blocked: ["metadata_unavailable"] };
  }

  const guard = evaluateCommitGuard(withTokens, {
    cfg,
    force,
    hasLock: true,
    pendingAddMessages: forSession.messages.length,
    pendingCommits: forSession.commits.length,
    openTurn,
    now,
  });
  if (!guard.ok) {
    return { agent: withTokens, committed: 0, blocked: guard.reasons };
  }

  if (remaining(deadline) <= 0) {
    return { agent: withTokens, committed: 0, blocked: ["budget"] };
  }

  const body = { keep_recent_count: 0 };
  const result = await commitAgentSession(fetchJSON, ovSessionId, log, body);
  if (result.ok) {
    log("commit", { sessionId: ovSessionId, mode, accepted: true });
    return {
      agent: {
        ...withTokens,
        ledger: clearLedger(agent.ledger),
        commitTokenCount: undefined,
        commitPending: undefined,
        committedAt: now,
      },
      committed: 1,
      blocked: [],
    };
  }

  if (result.pendingQueued) {
    // Accepted into the queue, not by the server. The pending entry is the
    // record; `commitPending` carries its dedup key so a later replay can
    // recognise its own settlement and nothing else.
    return {
      agent: {
        ...withTokens,
        commitPending: {
          ovSessionId,
          agentId: agent.agentId,
          dedupKey: result.dedupKey,
          createdAt: now,
          body,
        },
      },
      committed: 0,
      blocked: ["commit_queued"],
    };
  }

  const retryable = isRetryableFailure(result);
  return {
    agent: {
      ...withTokens,
      lastError: { at: now, kind: retryable ? "commit_retryable" : "commit_failed" },
      captureFailed: retryable
        ? agent.captureFailed
        : captureFailureRecord({
          cfg,
          kind: "commit_failed",
          errorKind: result?.error?.code || "unknown",
          at: now,
        }),
    },
    committed: 0,
    blocked: [retryable ? "commit_retryable" : "commit_failed"],
  };
}

/**
 * The two readings that decide whether an open turn may be closed as a
 * recovery partial.
 *
 * Both must agree: the file must not have grown or been touched between the
 * reads, and the caller must hold the lock. A single stat is not enough — the
 * agent writes between records, and a size that happens to match says nothing
 * about a write that is about to land.
 */
export async function fileIsQuiet(wirePath, { settleMs = 250, deadline } = {}) {
  let first;
  try {
    first = await stat(wirePath);
  } catch {
    return false;
  }
  const wait = Math.min(settleMs, Math.max(0, (deadline ?? Date.now() + settleMs) - Date.now()));
  if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
  let second;
  try {
    second = await stat(wirePath);
  } catch {
    return false;
  }
  return first.size === second.size && first.mtimeMs === second.mtimeMs;
}

export { enqueueAgentPending };
