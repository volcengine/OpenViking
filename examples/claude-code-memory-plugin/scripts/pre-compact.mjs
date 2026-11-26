#!/usr/bin/env node

/**
 * PreCompact Hook for Claude Code.
 *
 * Fires before CC rewrites the transcript via /compact (manual or auto).
 *
 * Catch-up first: the turn in progress has had no Stop yet, so without it
 * nothing of that turn is on the server when the model, right after an
 * auto-compaction in the middle of a long turn, looks it up in its session
 * history. The Stop capture runs over the same transcript, in its own process
 * as the Stop hook's detached worker does. The hook waits for it up to
 * CATCH_UP_WAIT_MS; past that it stops waiting but never kills it (a kill
 * mid-POST could leave a stored batch with an unsaved cursor, sent again by
 * the next Stop), so the push lands in the live tail after the commit, as it
 * did before this catch-up existed.
 *
 * Then the persistent OV session is committed so pending messages become an
 * archive before the transcript is mutated. PreCompact has no
 * additionalContext output (platform-verified), so injection happens later in
 * session-start.mjs when source="compact".
 */

import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import {
  commitSession,
  deriveOvSessionId,
  enqueuePendingDirectly,
  isRetryableFailure,
  makeFetchJSON,
} from "./lib/ov-session.mjs";
import { runHookStage } from "./shared/agent-hook-runtime.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const { log, logError } = createLogger("pre-compact");
// After the catch-up wait, health + commit share what is left of the hook's
// 30s; a commit that does not make it is queued and replayed later.
const AFTER_CATCH_UP_TIMEOUT_MS = 4000;
const fetchJSON = makeFetchJSON({ ...loadConfig(), timeoutMs: AFTER_CATCH_UP_TIMEOUT_MS });

const CATCH_UP_WAIT_MS = (() => {
  const v = Number(process.env.OPENVIKING_CC_PRECOMPACT_CATCH_UP_WAIT_MS);
  return Number.isFinite(v) && v >= 0 ? Math.floor(v) : 20000;
})();
const AUTO_CAPTURE = join(dirname(fileURLToPath(import.meta.url)), "auto-capture.mjs");

function approve() {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
}

// The Stop hook's capture as a detached worker (OV_HOOK_WORKER keeps it from
// detaching again), so it can outlive this hook.
function catchUpTranscript(raw) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(process.execPath, [AUTO_CAPTURE], {
        detached: true,
        env: { ...process.env, OV_HOOK_WORKER: "1" },
        stdio: ["pipe", "ignore", "ignore"],
      });
    } catch (err) {
      resolve({ ok: false, error: err?.message });
      return;
    }
    const timer = setTimeout(() => {
      child.unref();
      child.stdin.unref?.();
      resolve({ ok: false, backgrounded: true });
    }, CATCH_UP_WAIT_MS);
    child.on("error", (err) => { clearTimeout(timer); resolve({ ok: false, error: err?.message }); });
    child.on("exit", (code, signal) => {
      clearTimeout(timer);
      resolve({ ok: code === 0, code, signal: signal || undefined });
    });
    child.stdin.on("error", () => {});
    child.stdin.end(raw);
  });
}

runHookStage({
  loadConfig,
  input: { tolerant: true },
  // Write-path hook: gated by autoCapture so that disabling capture also
  // disables the pending-message commits triggered here.
  gates: { enabled: (cfg) => cfg.autoCapture },
  envelope: approve,
  onSkip: (reason) => log("skip", { reason }),
}, async ({ sessionId, raw }) => {
  if (!sessionId) {
    log("skip", { reason: "no session_id" });
    return;
  }

  log("catch_up", await catchUpTranscript(raw));

  const ovSessionId = deriveOvSessionId(sessionId);
  const health = await fetchJSON("/health");
  if (!health.ok && isRetryableFailure(health)) {
    const queued = await enqueuePendingDirectly("commitSession", ovSessionId, {});
    log("commit", { ovSessionId, ok: false, queued: queued.ok, reason: "health_retryable" });
    return;
  }
  if (!health.ok) {
    logError("health_check", `non-retryable status ${health.status || "unknown"}`);
    return;
  }

  const res = await commitSession(fetchJSON, ovSessionId);
  log("commit", {
    ovSessionId,
    ok: res.ok,
    trace_id: res.traceId || res.result?.trace_id,
    queued: Boolean(res.pendingQueued),
    error: res.ok ? undefined : res.error?.message,
  });
}).catch((err) => { logError("uncaught", err); approve(); });
