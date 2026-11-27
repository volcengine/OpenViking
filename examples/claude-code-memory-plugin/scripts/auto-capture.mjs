#!/usr/bin/env node

/**
 * Auto-Capture Hook Script for Claude Code
 *
 * Triggered by Stop hook.
 * Reads transcript_path from stdin → extracts INCREMENTAL new turns since last
 * capture → pushes them to a PERSISTENT per-CC-session OpenViking session.
 *
 * Unlike the previous one-shot model (create→add→extract→delete every Stop),
 * this keeps a stable ovSessionId derived from the CC session_id. A commit at the
 * pending-token threshold drives archive + extract.
 * This preserves cross-turn context for the memory extractor, produces archives
 * naturally, and lets resume / PreCompact / SessionEnd reuse the same session.
 *
 * Incremental tracking: state file per CC session_id records capturedTurnCount.
 *
 * Ported from openclaw-plugin/ context-engine.ts + text-utils.ts
 * (extractNewTurnMessages).
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { extractCaptureTurns, parseTranscript } from "./cc-transcript.mjs";
import {
  commitSession,
  deriveOvSessionId,
  enqueuePendingDirectly,
  getSession,
  isRetryableFailure,
  makeFetchJSON,
} from "./lib/ov-session.mjs";
import { maybeDetach, readHookStdin } from "./lib/async-writer.mjs";
import { replayPending } from "./lib/pending-queue.mjs";
import { readJsonState, writeJsonState } from "./lib/state.mjs";
import { getEffectivePeerId } from "./lib/workspace-peer.mjs";
import { runHookStage, withAgentHookLock } from "./shared/agent-hook-runtime.mjs";
import { sendSessionMessages } from "./shared/batch-send.mjs";
import { buildCaptureMessage, hasCaptureTrigger } from "./shared/capture-utils.mjs";
import { thresholdCommitDue } from "./shared/session-model.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const baseCfg = loadConfig();
const { log, logError } = createLogger("auto-capture");
const fetchJSON = makeFetchJSON(baseCfg, "captureTimeoutMs");

const STATE_DIR = join(tmpdir(), "openviking-cc-capture-state");

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function approve(msg) {
  const out = { decision: "approve" };
  if (msg) out.systemMessage = msg;
  output(out);
}

function stateFilePath(sessionId) {
  const safe = sessionId.replace(/[^a-zA-Z0-9_-]/g, "_");
  return join(STATE_DIR, `${safe}.json`);
}

async function loadState(sessionId) {
  try {
    const data = await readFile(stateFilePath(sessionId), "utf-8");
    return { capturedTurnCount: 0, ...JSON.parse(data) };
  } catch {
    return { capturedTurnCount: 0 };
  }
}

// The cursor only moves forward unless a caller rewinds it on purpose: a
// capture that started from an older snapshot must not undo a newer one's.
async function saveState(sessionId, state, { rewind = false } = {}) {
  try {
    await mkdir(STATE_DIR, { recursive: true });
    let next = state;
    if (!rewind) {
      const current = await loadState(sessionId);
      if (current.capturedTurnCount > state.capturedTurnCount) {
        next = { ...state, capturedTurnCount: current.capturedTurnCount };
      }
    }
    await writeFile(stateFilePath(sessionId), JSON.stringify(next));
  } catch { /* best effort */ }
}

async function reconcileKnownFailedCapture(cfg, sessionId, ovSessionId, state) {
  if (
    state.capturedTurnCount <= 0
    || cfg.captureMode !== "semantic"
    || !cfg.captureAssistantTurns
  ) {
    return;
  }

  const lastCapture = readJsonState("last-capture.json");
  if (
    lastCapture?.cc_session_id !== sessionId
    || lastCapture?.ov_session_id !== ovSessionId
    || Number(lastCapture.turns_failed || 0) <= 0
    || Number(lastCapture.turns_captured || 0) > 0
    || Number(lastCapture.turns_queued || 0) > 0
  ) {
    return;
  }

  const res = await fetchJSON(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}`);
  if (!res.ok && res.status !== 404) {
    log("capture_state_verification_deferred", {
      sessionId,
      ovSessionId,
      status: res.status || 0,
    });
    return;
  }

  const meta = res.ok ? (res.result || {}) : {};
  const hasDurableCapture = Number(meta.message_count || 0) > 0
    || Number(meta.total_message_count || 0) > 0
    || Number(meta.commit_count || 0) > 0;
  if (!hasDurableCapture) {
    log("capture_state_rewound", {
      sessionId,
      ovSessionId,
      previousCapturedTurnCount: state.capturedTurnCount,
      status: res.status || 200,
    });
    state.capturedTurnCount = 0;
    await saveState(sessionId, state, { rewind: true });
  }
}

// ---------------------------------------------------------------------------
// Persistent-session capture
// ---------------------------------------------------------------------------

function toPayload(turn, peerId) {
  return peerId ? { ...turn.message, peer_id: peerId } : turn.message;
}

async function pushTurnsToOv(ovSessionId, turns, peerId = "") {
  const payloads = turns.map((turn) => toPayload(turn, peerId));
  const res = await sendSessionMessages(fetchJSON, ovSessionId, payloads, {
    enqueueOnRetryable: true,
  });
  return {
    ok: res.sent,
    queued: res.queued,
    failed: res.failed,
    enqueueFailed: res.enqueueFailed,
    retryLater: res.retryLater,
    lastError: res.lastError,
  };
}

async function enqueueTurnsToPending(ovSessionId, turns, peerId = "") {
  let queued = 0;
  let failed = 0;
  for (const turn of turns) {
    const res = await enqueuePendingDirectly("addMessage", ovSessionId, toPayload(turn, peerId), fetchJSON.origin);
    if (res.ok) queued++;
    else failed++;
  }
  return { ok: 0, queued, failed, enqueueFailed: failed };
}

function isMissingSessionState(error) {
  const code = String(error?.code || "").toUpperCase();
  return code === "NOT_FOUND";
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

function userWroteTrigger(turn) {
  if (turn.message.role !== "user") return false;
  return hasCaptureTrigger(turn.message.parts.filter((p) => p.type === "text").map((p) => p.text).join("\n"));
}

// last_assistant_message is the text of the turn's final message, which
// follows its last tool result; the transcript writes each block as a line.
function holdsAnswer(turns, answer) {
  const start = turns.findLastIndex((turn) => turn.role === "user") + 1;
  const text = turns.slice(start).flatMap((turn) => turn.parts)
    .filter((part) => part.type === "text").map((part) => part.text).join("");
  return text.replace(/\s+/g, "") === answer.replace(/\s+/g, "");
}

async function main({ cfg, input, cwd }) {
  const transcriptPath = input.transcript_path;
  const sessionId = input.session_id || "unknown";

  const ovSessionId = sessionId !== "unknown" ? deriveOvSessionId(sessionId) : null;
  const effectivePeer = getEffectivePeerId(cfg, { sessionId, cwd });
  log("start", { sessionId, ovSessionId, transcriptPath, peerSource: effectivePeer.source });

  if (!transcriptPath || !ovSessionId) {
    log("skip", { stage: "input_check", reason: "no transcript_path or session_id" });
    return;
  }

  let transcriptContent;
  try {
    transcriptContent = await readFile(transcriptPath, "utf-8");
  } catch (err) {
    logError("transcript_read", err);
    return;
  }

  if (!transcriptContent.trim()) {
    log("skip", { stage: "transcript_read", reason: "empty transcript" });
    return;
  }

  const messages = parseTranscript(transcriptContent);
  const allTurns = extractCaptureTurns(messages, cfg);
  if (allTurns.length === 0) {
    log("skip", { stage: "transcript_parse", reason: "no user/assistant turns found" });
    return;
  }

  const state = await loadState(sessionId);
  await reconcileKnownFailedCapture(cfg, sessionId, ovSessionId, state);
  const newTurns = allTurns.slice(state.capturedTurnCount);
  // Claude Code writes the transcript asynchronously, so at Stop it may not
  // hold the final answer yet. Taking it from the payload is left out of the
  // cursor: should the transcript catch up later, the answer goes out twice
  // rather than not at all.
  const finalAnswer = cfg.captureLastAssistantOnStop
    ? String(input.last_assistant_message || "").trim()
    : "";
  if (finalAnswer && !holdsAnswer(allTurns, finalAnswer)) {
    newTurns.push({
      role: "assistant",
      parts: [{ type: "text", text: finalAnswer }],
      message: buildCaptureMessage({ role: "assistant", content: finalAnswer }, "assistant", cfg),
    });
  }
  // The cursor counts host-authored turns; only the conversation is sent.
  const captureTurns = newTurns.filter((turn) => !turn.hostAuthored && turn.message);
  log("transcript_parse", {
    totalTurns: allTurns.length,
    previouslyCaptured: state.capturedTurnCount,
    newTurns: newTurns.length,
    captureTurns: captureTurns.length,
    turnsSkipped: newTurns.length - captureTurns.length,
  });

  if (newTurns.length === 0) {
    log("skip", { stage: "incremental_check", reason: "no new turns" });
    return;
  }

  if (captureTurns.length === 0) {
    await saveState(sessionId, {
      ...state,
      capturedTurnCount: allTurns.length,
    });
    log("state_update", { newCapturedTurnCount: allTurns.length, reason: "nothing_to_send" });
    return;
  }

  // Keyword mode needs a trigger in what the user wrote in this batch.
  if (cfg.captureMode === "keyword" && !captureTurns.some(userWroteTrigger)) {
    log("skip", { stage: "keyword_mode_no_trigger", turns: captureTurns.length });
    await saveState(sessionId, {
      ...state,
      capturedTurnCount: allTurns.length,
    });
    return;
  }

  const health = await fetchJSON("/health");
  let result;
  if (health.ok) {
    // SessionStart replays at most one capped batch, so drain more of the
    // queue here; older entries go out before this turn's messages.
    try {
      await replayPending(fetchJSON, log, { consumeRetries: false });
    } catch (err) {
      logError("pending-replay", err);
    }
    result = await pushTurnsToOv(ovSessionId, captureTurns, effectivePeer.peerId);
  } else if (isRetryableFailure(health)) {
    logError("health_check", "server unreachable or unhealthy; enqueuing capture");
    result = await enqueueTurnsToPending(ovSessionId, captureTurns, effectivePeer.peerId);
    log("push_turns", {
      ovSessionId,
      ok: result.ok,
      queued: result.queued,
      failed: result.failed,
    });
    if (result.failed > 0) {
      logError("pending_enqueue", "some turns failed to enqueue; state not advanced");
      return;
    }
    await saveState(sessionId, {
      ...state,
      capturedTurnCount: allTurns.length,
    });
    log("state_update", { newCapturedTurnCount: allTurns.length, reason: "pending_queued" });
    writeJsonState("last-capture.json", {
      turns_captured: 0,
      turns_queued: result.queued,
      turns_failed: 0,
      pending_tokens: 0,
      commit_threshold: cfg.commitTokenThreshold,
      committed: false,
      commit_count: 0,
      total_message_count: 0,
      ov_session_id: ovSessionId,
      cc_session_id: sessionId,
    });
    return result.queued > 0 ? `queued ${result.queued} turns to pending queue` : undefined;
  } else {
    logError("health_check", `non-retryable status ${health.status || "unknown"}`);
    return;
  }
  log("push_turns", {
    ovSessionId,
    ok: result.ok,
    queued: result.queued,
    failed: result.failed,
    enqueueFailed: result.enqueueFailed,
    retryLater: result.retryLater,
  });

  if (result.enqueueFailed > 0 || result.retryLater > 0 || (
    result.failed > 0
    && result.ok === 0
    && result.queued === 0
    && isMissingSessionState(result.lastError)
  )) {
    logError(
      "capture_write",
      "some turns were neither sent nor queued; state not advanced",
    );
    writeJsonState("last-capture.json", {
      turns_captured: result.ok,
      turns_queued: result.queued,
      turns_failed: result.failed + result.enqueueFailed,
      pending_tokens: 0,
      commit_threshold: cfg.commitTokenThreshold,
      committed: false,
      commit_count: 0,
      total_message_count: 0,
      ov_session_id: ovSessionId,
      cc_session_id: sessionId,
    });
    return;
  }

  // A missing live-message file is recoverable after the server is fixed, so
  // retain the cursor for that failure, as for refused credentials. A message
  // the server refused is not sent again.
  await saveState(sessionId, {
    ...state,
    capturedTurnCount: allTurns.length,
  });
  log("state_update", { newCapturedTurnCount: allTurns.length });

  // Client-driven commit (ported from openclaw-plugin/context-engine.ts:afterTurn):
  // poll pending_tokens and commit when the threshold is crossed, unless
  // serverAutoCommit left that commit to OpenViking.
  let committed = false;
  let commitTraceId = "";
  let pendingTokens = 0;
  let commitCount = 0;
  let totalMessageCount = 0;
  if (result.ok > 0) {
    const meta = await getSession(fetchJSON, ovSessionId);
    pendingTokens = Number(meta?.pending_tokens || 0);
    commitCount = Number(meta?.commit_count || 0);
    totalMessageCount = Number(meta?.total_message_count || 0);
    log("pending_tokens", { ovSessionId, pending: pendingTokens, threshold: cfg.commitTokenThreshold });
    if (await thresholdCommitDue(fetchJSON, ovSessionId, cfg, meta)) {
      const commitRes = await commitSession(fetchJSON, ovSessionId, { keep_recent_count: 0 });
      committed = commitRes.ok;
      commitTraceId = commitRes.traceId || commitRes.result?.trace_id || "";
      if (committed) commitCount += 1;
      log("commit", {
        ovSessionId,
        ok: commitRes.ok,
        trace_id: commitTraceId || undefined,
        pending: pendingTokens,
      });
    }
  }

  // Snapshot for the statusline. Lives across sessions; statusline reads it
  // alongside last-recall.json to show pending/committed counts. commit_count
  // is the running total of archives this session has produced — distinct
  // from `committed` (which is just whether THIS turn triggered a commit).
  writeJsonState("last-capture.json", {
    turns_captured: result.ok,
    turns_queued: result.queued,
    turns_failed: result.failed,
    pending_tokens: pendingTokens,
    commit_threshold: cfg.commitTokenThreshold,
    committed,
    commit_count: commitCount,
    total_message_count: totalMessageCount,
    ov_session_id: ovSessionId,
    cc_session_id: sessionId,
  });

  // Cross-session daily counter — number of archives produced today across
  // all CC sessions. Cheap proxy for "how much OV digested today" without
  // hitting the server again. Resets on date rollover.
  if (committed) {
    const today = new Date().toISOString().slice(0, 10); // YYYY-MM-DD, UTC
    const prior = readJsonState("daily-stats.json") || {};
    const archives = prior.date === today ? Number(prior.archives || 0) + 1 : 1;
    writeJsonState("daily-stats.json", { date: today, archives });
  }

  if (result.ok === 0) return undefined;
  return `captured ${result.ok} turns to ov session ${ovSessionId}`
    + (committed
      ? ` (committed${commitTraceId ? `; trace_id=${commitTraceId}` : ""})`
      : "");
}

// One capture per CC session at a time, from reading the transcript to the
// last cursor write: the Stop worker and the PreCompact catch-up read the same
// cursor and would push the same turns. The wait covers one capture in flight
// at its request timeouts; the holder keeps the lock fresh, so only a crashed
// one goes stale. A capture that cannot get it leaves the turns to the next.
async function captureLocked(stage) {
  const sessionId = stage.input.session_id;
  if (!sessionId) return main(stage);
  const ran = await withAgentHookLock("claude-code", sessionId, async () => ({ value: await main(stage) }), {
    waitMs: 2 * stage.cfg.captureTimeoutMs,
    staleMs: 60_000,
    heartbeatMs: 10_000,
  });
  if (!ran) {
    log("skip", { stage: "lock", reason: "another capture of this session is still running", sessionId });
    return undefined;
  }
  return ran.value;
}

async function start() {
  // Write-path hook: gated by autoCapture so that disabling capture also stops
  // the transcript push. This runs against the hook's own directory, before the
  // payload names the session's.
  if (!baseCfg.autoCapture) {
    log("skip", { stage: "init", reason: "disabled" });
    approve();
    return;
  }

  // Async write path: parent detaches and returns, worker continues below.
  if (await maybeDetach(baseCfg, { approve })) return;

  await runHookStage({
    loadConfig,
    input: { read: readHookStdin },
    gates: { enabled: (cfg) => cfg.autoCapture },
    envelope: approve,
    onSkip: (reason) => log("skip", { stage: "init", reason }),
  }, captureLocked);
}

start().catch((err) => { logError("uncaught", err); approve(); });
