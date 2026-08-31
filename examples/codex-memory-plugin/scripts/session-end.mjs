#!/usr/bin/env node

/**
 * SessionEnd hook for Codex (Codex >= 0.145).
 *
 * Fires when a codex thread shuts down gracefully (`/quit`, `/exit`, double
 * Ctrl-C, EOF, `codex exec` completion); at TUI exit every thread touched in
 * the process gets one in a burst. This is the deterministic commit point:
 * catch up whatever turns the last Stop never sent, then commit the OV
 * session so the extractor runs on the whole conversation.
 *
 * Signals, crashes, Codex < 0.145 and app-server thread deferral never fire
 * it — the SessionStart sweep is the fallback for those.
 *
 * Budget: Codex allows 1s by default and clamps `timeout` to 3s, so the
 * parent only writes the `.ended` marker (lock-free) and detaches a worker,
 * regardless of `writePathAsync`. Codex deliberately leaves cleanly detached
 * helpers running after the hook exits.
 *
 * SessionEnd output is ignored; we print `{}` for symmetry with the other hooks.
 */

import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { catchUpTurns, commitOvSession, hasCaptureKeyword, makeFetchJSON } from "./ov-session.mjs";
import {
  clearEnded,
  loadState,
  markEnded,
  readEndedAt,
  saveState,
  withSessionLock,
} from "./session-state.mjs";
import { maybeDetach, readHookStdin } from "./shared/async-writer.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("session-end", cfg);
let activePeerId = cfg.peerId || "";

const LOCK_WAIT_MS = (() => {
  const v = Number(process.env.OPENVIKING_CODEX_LOCK_WAIT_MS);
  return Number.isFinite(v) && v >= 0 ? Math.floor(v) : 120_000;
})();

const { fetchJSONRes, fetchJSON } = makeFetchJSON(cfg, { getActorPeerId: () => activePeerId });

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

async function finish(sessionId, transcriptPath, endToken, heartbeat) {
  // Verify the marker this worker was launched for before doing any work. A
  // marker that is gone means the thread was resumed; a different one belongs
  // to a later exit whose own worker will commit.
  const marker = await readEndedAt(sessionId);
  if (marker !== endToken) {
    log("superseded", { sessionId, token: endToken, marker: marker ?? null });
    return;
  }

  const state = await loadState(sessionId);
  activePeerId = cfg.peerId || state.workspacePeerId || resolveEffectivePeerId({ cfg, cwd: process.cwd() }).peerId;
  log("start", { sessionId, transcriptPath, hasPeer: Boolean(activePeerId) });

  const health = await fetchJSON("/health");
  if (!health) {
    // Keep the state and the end marker; the sweep retries once OV is back.
    logError("health_check", "server unreachable; end marker kept for the sweep");
    return;
  }

  const { newTurns, added, skipped, unreadable } = await catchUpTurns({
    state,
    transcriptPath,
    fetchJSONRes,
    activePeerId,
    cfg,
    log,
    logError,
    heartbeat,
    shouldSend: (turns) =>
      Boolean(state.ovSessionId) || cfg.captureMode !== "keyword" || hasCaptureKeyword(turns),
  });
  if (added > 0) log("appended_catchup", { ovSessionId: state.ovSessionId, added });

  // An unreadable transcript is not an empty one: the tail turns may still be
  // there. Keep the live id and the marker so the sweep retries.
  if (unreadable) {
    logError("transcript_unreadable", { ovSessionId: state.ovSessionId, transcriptPath });
    await saveState(state, { touch: false });
    return;
  }

  // Committing now would archive a session missing its tail turns and release
  // the live id. Keep the live id and the marker so the sweep retries; the
  // cursor already advanced past the prefix that landed.
  if (newTurns.length > 0 && !skipped && added < newTurns.length) {
    logError("append_incomplete", {
      ovSessionId: state.ovSessionId,
      attempted: newTurns.length,
      added,
    });
    await saveState(state, { touch: false });
    return;
  }

  if (!state.ovSessionId) {
    log("skip", { stage: "commit", reason: "no live OV session for this codex session" });
    if (added > 0) await saveState(state, { touch: false });
    await clearEnded(sessionId, { before: endToken + 1 });
    return;
  }

  const ovSessionId = state.ovSessionId;
  const commit = await commitOvSession(fetchJSONRes, ovSessionId);
  if (!commit.ok) {
    log("commit", {
      reason: "session_end",
      ovSessionId,
      ok: false,
      status: commit.status,
      trace_id: commit.traceId,
      error: commit.error?.message || commit.error?.code,
    });
    // Keep ovSessionId and the end marker so the sweep retries this session.
    await saveState(state, { touch: false });
    return;
  }

  const traceId = commit.traceId || commit.result?.trace_id || "";
  log("commit", {
    reason: "session_end",
    ovSessionId,
    archived: commit.result?.archived ?? false,
    taskId: commit.result?.task_id,
    status: commit.result?.status,
    trace_id: traceId || undefined,
  });
  state.ovSessionId = null;
  await saveState(state, { touch: false });
  await clearEnded(sessionId, { before: endToken + 1 });
}

async function main() {
  if (!cfg.autoCapture) {
    log("skip", { stage: "init", reason: "autoCapture disabled" });
    output({});
    return;
  }

  let raw;
  let input;
  try {
    raw = await readHookStdin();
    input = JSON.parse(raw);
  } catch {
    log("skip", { stage: "stdin_parse", reason: "invalid input" });
    output({});
    return;
  }

  const sessionId = input.session_id;
  const transcriptPath = input.transcript_path || null;
  if (!sessionId) {
    log("skip", { stage: "init", reason: "no session_id" });
    output({});
    return;
  }

  // Lock-free and first: even if the worker never runs (job-object kill on
  // Windows, crash, disabled server) the sweep can still find and commit this
  // session at the next SessionStart. The marker's timestamp is the token the
  // worker verifies before committing, inherited through the environment.
  const inherited = Number(process.env.OPENVIKING_SESSION_END_TOKEN);
  const endToken = process.env.OV_HOOK_WORKER === "1" && Number.isFinite(inherited) && inherited > 0
    ? inherited
    : await markEnded(sessionId);
  process.env.OPENVIKING_SESSION_END_TOKEN = String(endToken);

  if (process.env.OV_HOOK_WORKER !== "1") {
    // stdin is already drained; hand the payload to the worker through the
    // shared cache env var.
    process.env.OPENVIKING_HOOK_STDIN_CACHE = raw;
    const detached = await maybeDetach(
      { ...cfg, writePathAsync: true },
      { approve: () => output({}) },
    );
    if (detached) return;
    delete process.env.OPENVIKING_HOOK_STDIN_CACHE;
    logError("detach_failed", "running the commit inline; Codex may kill it at the timeout");
  }

  const outcome = await withSessionLock(
    sessionId,
    ({ heartbeat }) => finish(sessionId, transcriptPath, endToken, heartbeat),
    { waitMs: LOCK_WAIT_MS },
  );
  if (outcome.skipped) {
    logError("lock_timeout", `another writer holds ${sessionId}; end marker left for the sweep`);
  }
  output({});
}

main().catch((err) => { logError("uncaught", err); output({}); });
