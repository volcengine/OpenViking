#!/usr/bin/env node

/**
 * Stop hook for Codex (turn end).
 *
 * Codex passes JSON on stdin including session_id, transcript_path,
 * last_assistant_message. Stop fires per turn — NOT at session end.
 *
 * Strategy:
 *   1. For this codex session_id, derive one long-lived OpenViking session
 *      id (`cx-<codex-session-id>`) and remember it in state.
 *   2. Read transcript_path, parse JSONL rollout, append every new
 *      user/assistant turn since last capture via add_message.
 *   3. If session pending_tokens crosses commitTokenThreshold, commit while
 *      keeping a recent live tail for continuity.
 *
 * A Stop for this session also proves the thread is alive again, so the
 * SessionEnd marker (if any) is cleared before anything else. Committing is
 * SessionEnd's job; PreCompact still commits before context compaction and
 * the SessionStart sweep remains the fallback for exits that never fire it.
 *
 * Stop output schema accepts {} as a no-op.
 */

import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { catchUpTurns, hasCaptureKeyword, makeFetchJSON } from "./ov-session.mjs";
import { clearEnded, loadState, saveState, withSessionLock } from "./session-state.mjs";
import { maybeDetach, readHookStdin } from "./shared/async-writer.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("auto-capture", cfg);
let activePeerId = cfg.peerId || "";

const LOCK_WAIT_MS = 120_000;

// A detached worker can boot long after the turn ended, so it clears end
// markers against the parent's start time rather than its own.
const HOOK_STARTED_AT = (() => {
  const inherited = Number(process.env.OPENVIKING_HOOK_STARTED_AT);
  return Number.isFinite(inherited) && inherited > 0 ? inherited : Date.now();
})();

const { fetchJSONRes, fetchJSON } = makeFetchJSON(cfg, { getActorPeerId: () => activePeerId });

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function noop(message) {
  output(message ? { systemMessage: message } : {});
}

async function maybeCommitByThreshold(ovSessionId, added) {
  const empty = {
    committed: false,
    pendingTokens: 0,
    commitCount: 0,
    totalMessageCount: 0,
    traceId: "",
  };
  if (added <= 0) return empty;
  const meta = await fetchJSON(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}`);
  const pendingTokens = Number(meta?.pending_tokens || 0);
  const commitCount = Number(meta?.commit_count || 0);
  const totalMessageCount = Number(meta?.total_message_count || 0);
  log("pending_tokens", {
    ovSessionId,
    pending: pendingTokens,
    threshold: cfg.commitTokenThreshold,
    keepRecentCount: cfg.commitKeepRecentCount,
  });
  if (pendingTokens < cfg.commitTokenThreshold) {
    return { committed: false, pendingTokens, commitCount, totalMessageCount, traceId: "" };
  }
  const commit = await fetchJSONRes(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}/commit`, {
    method: "POST",
    body: JSON.stringify({ keep_recent_count: cfg.commitKeepRecentCount }),
  });
  const committed = commit.ok;
  const traceId = commit.traceId || commit.result?.trace_id || "";
  log("commit", {
    ovSessionId,
    ok: committed,
    status: commit.status,
    trace_id: traceId || undefined,
    pending: pendingTokens,
    error: committed ? undefined : commit.error?.message || commit.error?.code,
  });
  return {
    committed,
    pendingTokens,
    commitCount: committed ? commitCount + 1 : commitCount,
    totalMessageCount,
    traceId,
  };
}

async function capture(sessionId, transcriptPath, heartbeat) {
  const state = await loadState(sessionId);
  activePeerId = cfg.peerId || state.workspacePeerId || resolveEffectivePeerId({ cfg, cwd: process.cwd() }).peerId;
  log("start", { sessionId, transcriptPath, hasPeer: Boolean(activePeerId) });

  const health = await fetchJSON("/health");
  if (!health) {
    logError("health_check", "server unreachable or unhealthy");
    return "";
  }

  const { added, ovSessionId } = await catchUpTurns({
    state,
    transcriptPath,
    fetchJSONRes,
    activePeerId,
    cfg,
    log,
    logError,
    heartbeat,
    shouldSend: (turns) => cfg.captureMode !== "keyword" || hasCaptureKeyword(turns),
  });

  let commitInfo = { committed: false, traceId: "" };
  if (added > 0) {
    log("appended", { ovSessionId, added });
    commitInfo = await maybeCommitByThreshold(ovSessionId, added);
  }

  await saveState(state);

  if (added <= 0) return "";
  return `appended ${added} turn(s) to OpenViking session ${state.ovSessionId}` +
    (commitInfo.committed
      ? ` (committed${commitInfo.traceId ? `; trace_id=${commitInfo.traceId}` : ""})`
      : "");
}

async function main() {
  if (!cfg.autoCapture) {
    log("skip", { stage: "init", reason: "autoCapture disabled" });
    noop();
    return;
  }

  // Async write mode returns a no-op response immediately; worker stdout is
  // intentionally discarded, so appended-count systemMessage is sync-only.
  process.env.OPENVIKING_HOOK_STARTED_AT = String(HOOK_STARTED_AT);
  if (await maybeDetach(cfg, { approve: () => output({}) })) return;

  let input;
  try {
    input = JSON.parse(await readHookStdin());
  } catch {
    log("skip", { stage: "stdin_parse", reason: "invalid input" });
    noop();
    return;
  }

  const sessionId = input.session_id || "unknown";
  const transcriptPath = input.transcript_path || null;

  // A turn ended for this session, so it is alive again after any resume — but
  // only for markers older than this hook run.
  await clearEnded(sessionId, { before: HOOK_STARTED_AT });

  const outcome = await withSessionLock(
    sessionId,
    ({ heartbeat }) => capture(sessionId, transcriptPath, heartbeat),
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
