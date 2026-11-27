import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const AUTO_CAPTURE = join(dirname(fileURLToPath(import.meta.url)), "..", "auto-capture.mjs");

// Runs the Stop hook's capture over the hook payload `raw` as a detached
// worker (OV_HOOK_WORKER keeps it from detaching again), waiting up to
// `waitMs`. Past that it stops waiting but never kills the worker: a kill
// mid-POST could leave a stored batch with an unsaved cursor, sent again by
// the next Stop.
export function catchUpTranscript(raw, waitMs) {
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
    }, waitMs);
    child.on("error", (err) => { clearTimeout(timer); resolve({ ok: false, error: err?.message }); });
    child.on("exit", (code, signal) => {
      clearTimeout(timer);
      resolve({ ok: code === 0, code, signal: signal || undefined });
    });
    child.stdin.on("error", () => {});
    child.stdin.end(raw);
  });
}
