import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { isCodexAvailable, trySpawnCodex } from "./codex-launch.mjs";
import { buildCodexExecArgs, fallbackRecallCompressorProfile, loadCachedRecallCompressorProfile, markRecallCompressorRuntimeFailed } from "./recall-compressor-profile.mjs";

export async function createCodexCompressor(cfg, { log = () => {}, logError = () => {}, onActiveChild = () => {} } = {}) {
  if (!["client", "auto"].includes(cfg.recallRewrite) || !isCodexAvailable()) return null;
  const profile = await loadCachedRecallCompressorProfile(cfg) || fallbackRecallCompressorProfile(cfg);
  if (!profile.enabled) return null;
  log("compress_profile", profile);
  let available = true;
  return async (prompt) => {
    if (!available) return null;
    const output = await runCodexCompressor(prompt, profile, cfg, { logError, onActiveChild });
    // A legacy-peer pass in this hook must not launch a model that just failed.
    if (output === null) available = false;
    return output;
  };
}

async function runCodexCompressor(prompt, profile, cfg, { logError, onActiveChild }) {
  const tmp = await mkdtemp(join(tmpdir(), "ov-recall-compress-"));
  const outputPath = join(tmp, "last-message.txt");
  const args = buildCodexExecArgs(profile, outputPath, cfg);

  try {
    return await new Promise((resolve) => {
      const env = {
        ...process.env,
        OPENVIKING_AUTO_RECALL: "0",
        OPENVIKING_AUTO_CAPTURE: "0",
        OPENVIKING_RECALL_COMPRESS: "0",
      };
      let child = null;
      let timer = null;
      let done = false;
      let timedOut = false;
      let stderr = "";
      const finish = (value, { runtimeFailed = false } = {}) => {
        if (done) return;
        done = true;
        onActiveChild(null);
        clearTimeout(timer);
        if (runtimeFailed) {
          // Mark the profile as runtime_failed so subsequent UPS calls in
          // this same codex session skip compress (avoids burning
          // ~recallCompressTimeoutMs per turn on a guaranteed-to-fail
          // spawn). Next SessionStart's cache-first detect treats this
          // marker as a cache miss and re-resolves against the current
          // catalogue, so a transient failure self-recovers across codex
          // restarts. Best-effort write; failure is non-fatal.
          markRecallCompressorRuntimeFailed(cfg, { failedModel: profile.model || "" })
            .catch(() => {});
        }
        resolve(value);
      };
      const launch = trySpawnCodex(args, { env, stdio: ["pipe", "ignore", "pipe"] });
      if (launch.error) {
        logError("compress_spawn", launch.error);
        finish(null, { runtimeFailed: true });
        return;
      }
      child = launch.child;
      onActiveChild(child);
      timer = setTimeout(() => {
        timedOut = true;
        logError("compress_timeout", `timed out after ${cfg.recallCompressTimeoutMs}ms`);
        try {
          child.kill("SIGKILL");
        } catch { /* best effort */ }
      }, cfg.recallCompressTimeoutMs);

      child.stderr.on("data", (chunk) => {
        stderr += chunk.toString();
        if (stderr.length > 4000) stderr = stderr.slice(-4000);
      });
      child.on("error", (err) => {
        logError("compress_spawn", err);
        finish(null, { runtimeFailed: true });
      });
      child.on("close", async (code) => {
        if (timedOut) {
          finish(null, { runtimeFailed: true });
          return;
        }
        if (code !== 0) {
          logError("compress_exit", {
            profile,
            error: stderr.trim().slice(-1000) || `codex exited ${code}`,
          });
          finish(null, { runtimeFailed: true });
          return;
        }
        try {
          finish(await readFile(outputPath, "utf-8"));
        } catch (err) {
          logError("compress_read", err);
          finish(null, { runtimeFailed: true });
        }
      });
      child.stdin.end(prompt);
    });
  } finally {
    await rm(tmp, { recursive: true, force: true }).catch(() => {});
  }
}

