/**
 * Async write-path helper.
 *
 * When `cfg.writePathAsync` is true and we're not already the worker, we:
 *   1. Drain the parent's stdin (the hook payload from CC)
 *   2. Emit `{decision:"approve"}` on stdout so CC unblocks immediately
 *   3. Spawn a detached clone of ourselves with env OV_HOOK_WORKER=1
 *   4. Feed the drained payload to the worker's stdin, unref, and exit
 *
 * The worker re-enters the same script, maybeDetach returns false (because
 * OV_HOOK_WORKER=1), and the hook's normal synchronous code path reads its
 * own stdin and runs the OV HTTP work with nobody waiting.
 *
 * Call this BEFORE the hook reads stdin itself.  When it returns true, the
 * caller must `return` out of main() immediately.
 */

import { spawn } from "node:child_process";

const WORKER_ENV = "OV_HOOK_WORKER";

export async function maybeDetach(cfg, { approve }) {
  if (!cfg.writePathAsync) return false;
  if (process.env[WORKER_ENV] === "1") return false;

  // Drain parent stdin so we can forward to the worker.
  let raw;
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    raw = Buffer.concat(chunks);
  } catch {
    // stdin read failed — let the synchronous path handle it (safer than
    // silently losing the event).
    return false;
  }

  let child;
  try {
    child = spawn(process.execPath, [process.argv[1]], {
      detached: true,
      stdio: ["pipe", "ignore", "ignore"],
      env: { ...process.env, [WORKER_ENV]: "1" },
    });
  } catch {
    // spawn failed — fall through to sync mode, but we've already drained
    // stdin so hand the payload back via env var path below.
    process.env.OPENVIKING_HOOK_STDIN_CACHE = raw.toString();
    return false;
  }

  // Approve CC first, then write to the detached child. Ordering matters:
  // CC reads our stdout before waiting for exit, so approving here is what
  // unblocks the user turn.
  approve();

  try {
    child.stdin.write(raw);
    child.stdin.end();
  } catch { /* worker may have already exited; nothing useful to recover */ }
  child.unref();
  return true;
}

/**
 * Fallback stdin reader used when the async path drained stdin but spawn
 * failed; hooks should call this instead of reading stdin directly when
 * possible so the sync fallback still works after a failed detach.
 */
export async function readHookStdin() {
  if (process.env.OPENVIKING_HOOK_STDIN_CACHE) {
    const cached = process.env.OPENVIKING_HOOK_STDIN_CACHE;
    delete process.env.OPENVIKING_HOOK_STDIN_CACHE;
    return cached;
  }
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString();
}
