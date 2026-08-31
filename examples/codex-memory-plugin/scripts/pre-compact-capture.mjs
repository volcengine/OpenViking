#!/usr/bin/env node

/**
 * PreCompact hook for Codex.
 *
 * Codex is about to summarize/compact the conversation. We commit the
 * long-lived OpenViking session for this codex session_id (Stop hooks
 * have already been appending turns), which triggers OV's memory
 * extractor on the full pre-compact transcript.
 *
 * Catch-up: if the transcript has new turns the Stop hook hasn't
 * appended yet, we append them before committing.
 *
 * After commit, we clear ovSessionId from state but keep
 * capturedTurnCount so post-compact Stop hooks don't re-capture pre-
 * compact turns. The next Stop will append to the same deterministic
 * `cx-<codex-session-id>` OV session id; `/messages` auto-creates it if
 * needed.
 *
 * This hook runs synchronously under a 60s budget, so it waits only a
 * bounded time for the per-session lock; if another writer holds it, we
 * leave everything alone and let SessionEnd or the sweep commit later.
 *
 * PreCompact output schema accepts {} as a no-op.
 */

import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { catchUpTurns, commitOvSession, hasCaptureKeyword, makeFetchJSON } from "./ov-session.mjs";
import { clearEnded, loadState, saveState, withSessionLock } from "./session-state.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("pre-compact", cfg);
let activePeerId = cfg.peerId || "";

// Well inside the 60s hook budget, leaving room for the commit itself.
const LOCK_WAIT_MS = (() => {
  const v = Number(process.env.OPENVIKING_CODEX_LOCK_WAIT_MS);
  return Number.isFinite(v) && v >= 0 ? Math.floor(v) : 40_000;
})();

const HOOK_STARTED_AT = Date.now();

const { fetchJSONRes, fetchJSON } = makeFetchJSON(cfg, { getActorPeerId: () => activePeerId });

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function noop(message) {
  output(message ? { systemMessage: message } : {});
}

async function compact(sessionId, transcriptPath, trigger, heartbeat) {
  const state = await loadState(sessionId);
  activePeerId = cfg.peerId || state.workspacePeerId || resolveEffectivePeerId({ cfg, cwd: process.cwd() }).peerId;
  log("start", { sessionId, transcriptPath, trigger, hasPeer: Boolean(activePeerId) });

  const health = await fetchJSON("/health");
  if (!health) {
    logError("health_check", "server unreachable");
    return "";
  }

  const { newTurns, added, ovSessionId, skipped, unreadable } = await catchUpTurns({
    state,
    transcriptPath,
    fetchJSONRes,
    activePeerId,
    cfg,
    log,
    logError,
    heartbeat,
    // Keyword mode only gates sessions that have nothing live yet; once an OV
    // session exists we always finish it before compaction.
    shouldSend: (turns) =>
      Boolean(state.ovSessionId) || cfg.captureMode !== "keyword" || hasCaptureKeyword(turns),
  });

  if (added > 0) log("appended_catchup", { ovSessionId, added });

  // An unreadable transcript is not an empty one: the tail turns may still be
  // there. Keep the live id and the marker so a later commit retries.
  if (unreadable) {
    logError("transcript_unreadable", { ovSessionId: state.ovSessionId, transcriptPath });
    await saveState(state, { touch: false });
    return `pre-compact transcript unreadable for ${state.ovSessionId || sessionId}; state preserved for retry`;
  }

  if (newTurns.length > 0 && !skipped && added < newTurns.length) {
    logError("append_failed_keep_state", { ovSessionId, attempted: newTurns.length, added });
    await saveState(state);
    return `pre-compact catch-up append incomplete for ${ovSessionId}; state preserved for retry`;
  }

  if (!state.ovSessionId) {
    log("skip", { stage: "commit", reason: "no OV session for this codex session" });
    await saveState(state);
    return "";
  }

  const liveOvSessionId = state.ovSessionId;
  const commit = await commitOvSession(fetchJSONRes, liveOvSessionId);

  // Commit failure handling (see DESIGN.md "Commit failure"): if /commit
  // fails (server unreachable, non-2xx, timeout) we MUST NOT reset
  // ovSessionId — keep state intact so SessionEnd / the sweep can retry.
  if (!commit.ok) {
    log("commit", {
      ovSessionId: liveOvSessionId,
      ok: false,
      status: commit.status,
      trace_id: commit.traceId,
      error: commit.error?.message || commit.error?.code,
    });
    await saveState(state);
    return `pre-compact commit attempted on ${liveOvSessionId}; result unavailable` +
      `${commit.traceId ? ` (trace_id=${commit.traceId})` : ""} (state preserved for retry)`;
  }

  const traceId = commit.traceId || commit.result?.trace_id || "";
  log("commit", {
    ovSessionId: liveOvSessionId,
    archived: commit.result?.archived ?? false,
    taskId: commit.result?.task_id,
    status: commit.result?.status,
    trace_id: traceId || undefined,
  });

  // Reset OV session for the post-compact half. Keep capturedTurnCount so
  // we don't re-capture pre-compact turns when Stop fires next.
  state.ovSessionId = null;
  await saveState(state);

  return `OpenViking session ${liveOvSessionId} is committed` + (traceId ? ` (trace_id=${traceId})` : "");
}

async function main() {
  if (!cfg.autoCommitOnCompact) {
    log("skip", { stage: "init", reason: "autoCommitOnCompact disabled" });
    noop();
    return;
  }

  let input;
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    input = JSON.parse(Buffer.concat(chunks).toString());
  } catch {
    log("skip", { stage: "stdin_parse", reason: "invalid input" });
    noop();
    return;
  }

  const sessionId = input.session_id || "unknown";
  const transcriptPath = input.transcript_path || null;
  const trigger = input.trigger || "auto";

  // Compaction means the thread is running, so any earlier end marker is stale.
  await clearEnded(sessionId, { before: HOOK_STARTED_AT });

  const outcome = await withSessionLock(
    sessionId,
    ({ heartbeat }) => compact(sessionId, transcriptPath, trigger, heartbeat),
    { waitMs: LOCK_WAIT_MS },
  );
  if (outcome.skipped) {
    logError("lock_timeout", `another writer holds ${sessionId}; leaving state untouched`);
    noop();
    return;
  }
  noop(outcome.value);
}

main().catch((err) => { logError("uncaught", err); noop(); });
