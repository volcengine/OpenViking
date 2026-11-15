/**
 * The plugin's own state, and the invariants that decide what may be sent.
 *
 * This file holds no I/O. It is the set of pure decisions the capture stage
 * asks before it writes anything, because each of them is a place where a
 * plausible-looking shortcut silently loses or duplicates a user's memory:
 *
 * - A cursor may only advance over a contiguous prefix that was *confirmed*.
 * - A commit may only happen when nothing in front of it is still unknown.
 * - A delivered message is not a committed one, and a queued one is neither.
 *
 * The state itself lives in one JSON file per native session, written through
 * the shared `writeHookState` (temp file + rename). Per agent, it carries the
 * reader cursor, the delivery cursor, the open turn's start, the branch edges,
 * the set of stable message ids already handed over, and the failure records.
 */

import { createHash } from "node:crypto";

import { summarizeLedger, unsettledRange } from "./turn-ledger.mjs";

/** The number of stable message ids one agent remembers. */
export const STABLE_ID_LIMIT = 4000;

/** How long an ended marker makes a state eligible for the sweep. */
export const ENDED_MARKER_TTL_MS = 30 * 24 * 60 * 60 * 1000;

/** The state shape for a session that has never run. */
export function emptySessionState() {
  return {
    version: 1,
    nativeSessionId: "",
    agents: {},
    endedMarker: undefined,
    endedResult: undefined,
    resumedAt: undefined,
    lastActivity: undefined,
    hasCaptureGaps: false,
    profileInjected: false,
    recallCache: undefined,
    recallFailures: 0,
    recallFailureUntil: 0,
    sweep: undefined,
    diagnostics: {},
  };
}

/** The per-agent record. */
export function emptyAgentState({ agentId, ovSessionId }) {
  return {
    agentId,
    ovSessionId,
    wirePath: undefined,
    protocolVersion: undefined,
    minProtocolVersion: undefined,
    fileIdentity: undefined,
    prefixHash: undefined,
    // Bytes decoded and processed locally.
    scanOffset: 0,
    // Last line number decoded, which is the branch tree's coordinate.
    scanLine: 0,
    // Contiguous prefix handed to the server or durably queued.
    ackOffset: 0,
    ackLine: 0,
    // Earliest offset that still has to be re-read to rebuild an open turn.
    pendingTurnOffset: 0,
    pendingTurnLine: 0,
    // Wire line of the turn currently open, if any.
    openTurnLine: undefined,
    branchEdges: [],
    activeBranch: "main",
    stableIds: [],
    // What has been handed over and what that proves. This replaces the two
    // message counters the first implementation used; see turn-ledger.mjs.
    ledger: undefined,
    commitPending: undefined,
    captureFailed: undefined,
    pendingLost: [],
    lastError: undefined,
    lastDeliveredAt: undefined,
    // A turn was read as recovery partial because the session ended.
    recoveryPartialAt: undefined,
    // Set when this agent's state must be read from the file end (takeover).
    takeoverAt: undefined,
    // A turn-sliced or copied fork inherits history this plugin must not import.
    forkBoundaryLine: undefined,
    hasCaptureGaps: false,
    diagnostics: {},
  };
}

/**
 * A non-secret fingerprint of the configuration that decides whether a
 * permanently failed capture may be retried.
 *
 * The design requires that a non-retryable failure is not retried on every
 * event, but is retried when the configuration that caused it changes. That
 * comparison must not store the API key or the URL's credentials, so the
 * fingerprint covers only the fields that change what is sent: the endpoint
 * (without userinfo), the capture shaping knobs, and the filter rules.
 */
export function configFingerprint(cfg = {}) {
  const url = String(cfg.baseUrl || "").replace(/\/\/[^@/]*@/, "//");
  // A change of credential *source* can fix a 401 without the key itself
  // changing: a user switches from a stale environment variable to ovcli.conf.
  // The fingerprint therefore covers where the key came from and who it claims
  // to be, as a digest of the identity, never of the key. A rotation that
  // leaves source and identity alone is deliberately not a retry trigger —
  // recognising it would mean storing the key, which is the thing to avoid.
  const identityDigest = createHash("sha256")
    .update(`${cfg.apiKeySource || ""}\n${cfg.credentialSource || ""}\n${cfg.account || ""}\n${cfg.user || ""}`)
    .digest("hex")
    .slice(0, 12);
  const material = JSON.stringify({
    url,
    identityDigest,
    captureMaxLength: cfg.captureMaxLength,
    captureToolMaxChars: cfg.captureToolMaxChars,
    captureFilters: cfg.captureFilters,
    captureAssistantTurns: cfg.captureAssistantTurns,
  });
  return createHash("sha256").update(material).digest("hex").slice(0, 16);
}

/**
 * May a permanently failed capture be attempted again?
 *
 * The record carries the fingerprint of the configuration that produced it. The
 * same configuration means the same failure: the cursor stays where it is and no
 * request is made. A different one means the user changed something that could
 * fix it, so the failure is lifted and the range is rebuilt from its start.
 */
export function captureFailureBlocks(agent, cfg) {
  const failure = agent?.captureFailed;
  if (!failure || failure.resolved) return false;
  return failure.configFingerprint === configFingerprint(cfg);
}

/** The record for a non-retryable failure, with everything needed to rebuild it. */
export function captureFailureRecord({
  cfg,
  kind,
  errorKind,
  wireStart,
  wireEnd,
  stableIds,
  at = Date.now(),
}) {
  return {
    at,
    kind,
    errorKind,
    wireStart: Number.isFinite(wireStart) ? wireStart : undefined,
    wireEnd: Number.isFinite(wireEnd) ? wireEnd : undefined,
    stableIds: Array.isArray(stableIds) ? stableIds.slice(0, 64) : undefined,
    configFingerprint: configFingerprint(cfg),
    resolved: false,
  };
}

/**
 * Advance a contiguous cursor.
 *
 * The cursor is a promise about a prefix: everything before it was handed over.
 * A gap must not be skipped, so this only moves forward, and only to a value
 * that is at least the current one. It returns the new value rather than
 * mutating, because the caller persists the whole state atomically.
 */
export function advanceCursor(current, candidate) {
  const from = Number(current) || 0;
  const to = Number(candidate);
  if (!Number.isFinite(to)) return from;
  return to > from ? to : from;
}

/** Remember a stable message id, dropping the oldest once the set is full. */
export function rememberStableIds(existing, added, limit = STABLE_ID_LIMIT) {
  const out = Array.isArray(existing) ? [...existing] : [];
  const seen = new Set(out);
  for (const id of added || []) {
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out.length > limit ? out.slice(out.length - limit) : out;
}

/** Has this message already been handed to the server (or the queue)? */
export function alreadyDelivered(stableIds, id) {
  return Array.isArray(stableIds) && stableIds.includes(id);
}

/**
 * What the capture stage may do with the messages it just mapped.
 *
 * `sent` and `queued` both advance the delivery cursor — queued means "durably
 * on this disk", and the replay obligation is recorded separately. Everything
 * else stops the cursor at the first payload that was not confirmed, which is
 * what keeps the queue a contiguous prefix.
 *
 * `enqueueFailed` and a non-retryable `failed` also block the commit: a
 * transcript with a hole in it must not be archived as if it were complete.
 */
export function deliveryOutcome(result, payloadCount) {
  const sent = Number(result?.sent) || 0;
  const queued = Number(result?.queued) || 0;
  const enqueueFailed = Number(result?.enqueueFailed) || 0;
  const failed = Number(result?.failed) || 0;
  const confirmed = Math.min(payloadCount, sent + queued);
  return {
    sent,
    queued,
    enqueueFailed,
    failed,
    confirmed,
    // The cursor may advance over these many payloads.
    advanceBy: confirmed,
    // The commit must wait.
    blocksCommit: enqueueFailed > 0 || failed > 0,
    // A non-retryable failure is recorded and not retried until config moves.
    nonRetryable: failed > 0 && result?.retryable === false,
  };
}

/**
 * The commit guard.
 *
 * Every condition here is a reason *not* to commit, and the caller must be able
 * to say which one stopped it — "no commit" with no reason is the state a
 * maintainer cannot debug. Conditions are evaluated in the design's order so
 * the first reported reason is the earliest problem.
 *
 * `force` is the PreCompact / SessionEnd boundary, which skips the thresholds
 * but nothing else.
 */
export function evaluateCommitGuard(agent, {
  cfg = {},
  force = false,
  hasLock = true,
  pendingAddMessages = 0,
  pendingCommits = 0,
  openTurn = false,
  now = Date.now(),
} = {}) {
  const reasons = [];
  if (!hasLock) reasons.push("no_lock");
  if (!agent) reasons.push("no_agent_state");

  const summary = summarizeLedger(agent?.ledger);
  // A normal boundary may only commit complete turns; a forced one may also
  // commit the terminal and recovery partials that are fully delivered.
  const committable = summary.completeSent + (force ? summary.terminalSent : 0);

  if (agent) {
    if (committable <= 0) reasons.push("nothing_delivered");
    if (openTurn) reasons.push("open_turn");
    if (pendingAddMessages > 0) reasons.push("pending_add_messages");
    if (pendingCommits > 0) reasons.push("pending_commit");
    // A failure blocks while it is unresolved *for this configuration*. Once
    // the user changes what could fix it, the retry is allowed.
    if (captureFailureBlocks(agent, cfg)) reasons.push("capture_failed");
    // Only an unaccepted loss blocks. An accepted one is a recorded fact for
    // diagnosis, not a permanent prohibition on ever committing again.
    if (Array.isArray(agent.pendingLost) && agent.pendingLost.some((item) => !item.accepted)) {
      reasons.push("pending_lost");
    }
    if (agent.commitPending) reasons.push("commit_already_pending");
    // The ledger dropped turns it can no longer prove. Committing on a floor
    // would archive a transcript whose middle nobody can vouch for.
    if (summary.overflow) reasons.push("ledger_overflow");
    // A resumed generation may not commit at all until it has delivered a new
    // complete turn of its own — not just when it has nothing to commit. An old
    // terminal partial or a replayed backlog turn must not seal the session the
    // user just reopened.
    if (agent.resumeBlockedCommit) reasons.push("resume_waiting_for_new_turn");
  }

  if (!force && agent) {
    const threshold = Number(cfg.commitTokenThreshold);
    const tokens = agent.commitTokenCount;
    // `null` means the metadata could not be read. That is not zero tokens, and
    // the caller has already refused to commit in that case; the turn fallback
    // is only for a server that answered without the field.
    const tokenHit = typeof tokens === "number" && Number.isFinite(threshold) && tokens >= threshold;
    const turnThreshold = Number(cfg.commitTurnThreshold);
    const turnHit = Number.isFinite(turnThreshold) && turnThreshold > 0 && summary.completeSent >= turnThreshold;
    if (!tokenHit && !turnHit) reasons.push("below_threshold");
  }

  return {
    ok: reasons.length === 0,
    reasons,
    uncommitted: committable,
    completeSent: summary.completeSent,
    terminalSent: summary.terminalSent,
    queued: summary.queued,
    force,
    at: now,
  };
}

/**
 * Whether a pending replay result settles this agent's commit.
 *
 * Only `commitSession` with status `sent` does. A `skipped` commit was dropped
 * by the queue — at its TTL, out of retries, or on a non-retryable answer — and
 * the data is gone; treating that as success would clear `commitPending` and
 * archive a transcript whose extraction never happened. A `deferred` one was
 * not attempted at all.
 */
export function commitSettledBy(result, commitPending) {
  if (!result || !commitPending) return false;
  if (result.type !== "commitSession") return false;
  if (result.status !== "sent") return false;
  // Both keys must be present and equal. A missing key on either side is not a
  // match: "I cannot tell whether this is mine" has to mean "keep waiting",
  // otherwise a commit for another generation settles this one.
  if (!result.dedupKey || !commitPending.dedupKey) return false;
  if (result.dedupKey !== commitPending.dedupKey) return false;
  if (!result.sessionId || !commitPending.ovSessionId) return false;
  if (result.sessionId !== commitPending.ovSessionId) return false;
  return true;
}

/**
 * Whether a replay result clears one queued message for this agent.
 *
 * A `sent` message is delivered. A `skipped` one was dropped and must become a
 * `pending_lost` record rather than a success. Anything else leaves the state
 * exactly as it was.
 */
export function classifyReplayResult(result, agent) {
  if (!result) return { effect: "none" };
  if (result.sessionId && agent?.ovSessionId && result.sessionId !== agent.ovSessionId) {
    return { effect: "none", reason: "other_session" };
  }
  if (result.type === "addMessage") {
    if (result.status === "sent") return { effect: "message_delivered" };
    if (result.status === "skipped") return { effect: "message_lost", errorKind: result.errorKind };
    return { effect: "none", reason: result.status };
  }
  if (result.type === "commitSession") {
    if (result.status === "sent") return { effect: "commit_delivered" };
    if (result.status === "skipped") return { effect: "commit_lost", errorKind: result.errorKind };
    return { effect: "none", reason: result.status };
  }
  return { effect: "none" };
}

/**
 * Does a state file belong to the sweep's attention?
 *
 * A state is eligible when it carries an ended marker and is not currently an
 * active resumed session. The resumed case is the subtle one: a marker written
 * before the user resumed must not let the sweep seal the session that is now
 * running, so `resumedAt > endedMarker.at` takes it out.
 */
export function isSweepCandidate(state, { now = Date.now(), ttlMs = ENDED_MARKER_TTL_MS } = {}) {
  if (!state || typeof state !== "object") return false;
  const marker = state.endedMarker;
  if (!marker || typeof marker.at !== "number") return false;
  if (typeof state.resumedAt === "number" && state.resumedAt > marker.at) return false;
  if (state.endedResult && state.endedResult.status === "partial_loss") return false;
  if (state.endedResult && state.endedResult.status === "completed") return false;
  if (now - marker.at > ttlMs) return false;
  return true;
}

/**
 * The state the ended sweep leaves behind.
 *
 * `allAgentsSettled` is the caller's verdict over every agent: delivered,
 * committed, or explicitly accepted as lost. Only then may the marker be
 * cleared, because clearing it early is what makes a half-captured session look
 * finished forever.
 */
export function settleEndedMarker(state, { allAgentsSettled, at = Date.now(), partialLoss = false } = {}) {
  if (!state?.endedMarker) return state;
  if (!allAgentsSettled) {
    return {
      ...state,
      endedResult: {
        at,
        status: partialLoss ? "partial_loss" : "incomplete",
        reason: state.endedResult?.reason,
      },
    };
  }
  return {
    ...state,
    endedMarker: undefined,
    endedResult: {
      at,
      status: partialLoss ? "partial_loss" : "completed",
      reason: state.endedResult?.reason,
    },
  };
}

/**
 * Is anything handed over but not committed?
 *
 * A queued message counts: it is durable locally and the server does not have
 * it yet, so the session is not settled.
 */
export function hasUncommittedDelivery(agent) {
  const summary = summarizeLedger(agent?.ledger);
  return summary.completeSent + summary.terminalSent > 0 || summary.queued > 0;
}

/**
 * Is this agent settled for the purpose of clearing an ended marker?
 *
 * Settled means: nothing uncommitted, no queued commit, no unresolved capture
 * failure, and no loss the user has not yet accepted. `hasCaptureGaps` is
 * deliberately not part of it — an accepted loss is a recorded fact for the
 * doctor, not a reason to keep a session open forever.
 */
export function agentSettled(agent) {
  if (!agent) return true;
  if (hasUncommittedDelivery(agent)) return false;
  if (agent.commitPending) return false;
  if (agent.captureFailed && !agent.captureFailed.resolved) return false;
  if ((agent.pendingLost || []).some((item) => !item.accepted)) return false;
  return true;
}

/** Does the pending queue still hold anything for this OpenViking session? */
export function pendingForSession(entries, ovSessionId) {
  const messages = [];
  const commits = [];
  for (const { filename, entry } of entries || []) {
    if (!entry || entry.sessionId !== ovSessionId) continue;
    if (entry.type === "addMessage") messages.push({ filename, entry });
    else if (entry.type === "commitSession") commits.push({ filename, entry });
  }
  return { messages, commits };
}

/**
 * A loss record for a message the queue dropped.
 *
 * The design requires a range and the stable ids so an operator can tell
 * exactly which part of a transcript is missing, and requires the record to be
 * attributable — a bare count cannot be reconciled against a wire file.
 */
export function pendingLostRecord({
  agentId,
  ovSessionId,
  dedupKey,
  type,
  errorKind,
  wireStart,
  wireEnd,
  stableIds,
  at = Date.now(),
}) {
  return {
    agentId,
    ovSessionId,
    dedupKey,
    type,
    errorKind,
    wireStart: Number.isFinite(wireStart) ? wireStart : undefined,
    wireEnd: Number.isFinite(wireEnd) ? wireEnd : undefined,
    stableIds: Array.isArray(stableIds) ? stableIds.slice(0, 64) : undefined,
    at,
    accepted: false,
  };
}

/**
 * Pick the agents an action applies to.
 *
 * Ambiguity is an error, not a default. Choosing "the first agent" would make
 * `accept-loss` silently discard data from a subagent the user did not know
 * existed, and `retry-commit` would clear the wrong blockage.
 */
export function selectAgents(state, { agent, allAgents }) {
  const ids = Object.keys(state.agents || {});
  if (ids.length === 0) return { error: "no agents in state" };
  if (allAgents) return { agents: ids };
  if (agent) {
    if (!ids.includes(agent)) return { error: `unknown agent '${agent}'; state has ${ids.join(", ")}` };
    return { agents: [agent] };
  }
  if (ids.length === 1) return { agents: ids };
  return { error: `session has ${ids.length} agents (${ids.join(", ")}); pass --agent <id> or --all-agents` };
}

/**
 * `retry-commit`: clear the blockage a lost commit left, and nothing else.
 *
 * The preconditions are the point. An agent with a queued message has data the
 * server never received; clearing its commit blockage would let the next
 * boundary archive a transcript that is missing the very message the queue
 * still holds. So this refuses, and says which condition failed.
 */
export function retryCommit(state, agentId, {
  reason = "",
  now = Date.now(),
  pendingMessages = 0,
  otherCommits = 0,
} = {}) {
  const agent = state.agents?.[agentId];
  if (!agent) return { ok: false, error: `unknown agent '${agentId}'` };

  // Every precondition below is a fact the caller read from the real queue, not
  // a field in this state: a state cannot know what another process queued.
  // Clearing a commit blockage while a message is still waiting would let the
  // next boundary archive a transcript that is missing it.
  if (pendingMessages > 0) {
    return {
      ok: false,
      error: `${pendingMessages} message(s) for this agent are still in the queue; replay them before retrying the commit`,
    };
  }
  if (agent.captureFailed && !agent.captureFailed.resolved && agent.captureFailed.kind !== "commit_failed") {
    return { ok: false, error: "agent has a capture failure; run accept-loss to record the loss" };
  }
  if (agent.commitPending) {
    return { ok: false, error: "agent already has a pending commit; wait for the queue to settle it" };
  }
  if (otherCommits > 0) {
    return { ok: false, error: "another commit for this session is still queued; settle it first" };
  }
  const lost = (agent.pendingLost || []).filter((item) => item.type === "commitSession" && !item.accepted);
  if (lost.length === 0 && !(agent.captureFailed && agent.captureFailed.kind === "commit_failed")) {
    return { ok: false, error: "no lost commit to retry" };
  }

  return {
    ok: true,
    state: {
      ...state,
      agents: {
        ...state.agents,
        [agentId]: {
          ...agent,
          captureFailed: agent.captureFailed ? { ...agent.captureFailed, resolved: true, resolvedAt: now } : undefined,
          pendingLost: (agent.pendingLost || []).filter((item) => !(item.type === "commitSession" && !item.accepted)),
          commitRetry: { at: now, reason, duplicatePossible: true },
        },
      },
      recoveries: [...(state.recoveries || []), { action: "retry-commit", agentId, at: now, reason }],
    },
    warning: "a commit that was accepted before the failure may be archived twice",
  };
}

/**
 * `accept-loss`: record that the missing data is gone, and move on.
 *
 * The tombstone is what keeps the loss visible: `hasCaptureGaps` stays set, the
 * record says what range was lost, and `endedResult` becomes `partial_loss`
 * only when every other agent is already settled. A session with one agent
 * accepted and another still outstanding keeps its marker, so the sweep can
 * still finish the rest.
 */
export function acceptLoss(state, agentIds, {
  reason = "",
  now = Date.now(),
  pendingMessagesByAgent = {},
} = {}) {
  let next = { ...state, agents: { ...state.agents } };
  const accepted = [];
  for (const agentId of agentIds) {
    const agent = next.agents[agentId];
    if (!agent) continue;

    // A message still in the queue is not lost: its outcome is unknown, and
    // recording it as lost would be a claim nobody can support. A message the
    // queue already reported as dropped *is* lost, and accepting that is the
    // whole point of this action — the queue no longer holds it, so there is
    // nothing left to replay.
    const stillQueued = Number(pendingMessagesByAgent[agentId]) || 0;
    const lostMessages = (agent.pendingLost || []).filter((item) => !item.accepted);
    const failure = agent.captureFailed && !agent.captureFailed.resolved ? agent.captureFailed : undefined;
    if (lostMessages.length === 0 && !failure) continue;

    const range = unsettledRange(agent.ledger);
    const wireEnd = Math.max(
      Number(failure?.wireEnd) || 0,
      Number(agent.ackLine) || 0,
      ...lostMessages.map((item) => Number(item.wireEnd) || 0),
    );

    next.agents[agentId] = {
      ...agent,
      // The cursor moves past the loss so the next pass does not re-read it,
      // and the tombstone records that the gap is deliberate.
      ackLine: Math.max(Number(agent.ackLine) || 0, wireEnd),
      // The byte cursor and the line cursor have to describe the same position.
      // Advancing only the line makes the next read start at an offset that
      // belongs to an earlier line, and the branch tree — which is keyed by
      // line — then mislabels every record after it.
      ackOffset: 0,
      // The accepted range is gone, so any open turn that started inside it can
      // never be completed. Forgetting it is what keeps the next read from
      // waiting for records that will not come.
      openTurnLine: undefined,
      pendingTurnOffset: 0,
      hasCaptureGaps: true,
      captureFailed: failure ? { ...failure, resolved: true, resolvedAt: now, accepted: true } : agent.captureFailed,
      pendingLost: (agent.pendingLost || []).map((item) => (
        item.accepted ? item : { ...item, accepted: true, acceptedAt: now }
      )),
      tombstone: {
        at: now,
        reason,
        wireEnd,
        wireStart: range?.wireStart,
        failure: failure ? { kind: failure.kind, errorKind: failure.errorKind } : undefined,
        lostCount: lostMessages.length,
        stillQueued,
      },
    };
    accepted.push(agentId);
  }

  if (accepted.length === 0) return { ok: false, error: "nothing to accept for the selected agents" };

  // "Settled" for this decision means nothing is *unknown*: no unaccepted loss,
  // no unresolved failure, no queued commit. A delivered-but-uncommitted turn is
  // not unknown — it is exactly what the next boundary will archive — so it must
  // not keep the session open forever.
  const agents = Object.values(next.agents);
  const othersSettled = agents.every((agent) => (
    !agent.commitPending
    && !(agent.captureFailed && !agent.captureFailed.resolved)
    && !(agent.pendingLost || []).some((item) => !item.accepted)
  ));

  next = {
    ...next,
    hasCaptureGaps: true,
    recoveries: [...(next.recoveries || []), { action: "accept-loss", agentIds: accepted, at: now, reason }],
  };
  if (othersSettled) {
    // `partial_loss` is the session's verdict only when every agent is settled.
    // One agent accepted while another still owes work keeps the marker, so the
    // sweep can still finish the rest.
    next.endedResult = { at: now, status: "partial_loss", reason };
    next.endedMarker = undefined;
  }

  return { ok: true, state: next, accepted, ended: othersSettled };
}
