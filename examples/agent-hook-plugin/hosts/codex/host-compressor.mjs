import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { isCodexAvailable, trySpawnCodex } from "./codex-launch.mjs";
import {
  buildCodexExecArgs,
  detectRecallCompressorProfile,
  fallbackRecallCompressorProfile,
  loadCachedRecallCompressorProfile,
  markRecallCompressorRuntimeFailed,
} from "./recall-compressor-profile.mjs";

// A model that failed to run is left alone this long before it is resolved again.
const RETRY_FAILED_MS = 30 * 60 * 1000;

/**
 * The recall compressor run through `codex exec`, or null when it cannot run.
 * The model profile is looked up in the cache first and resolved only when the
 * cache has none, or holds a failure older than RETRY_FAILED_MS.
 */
export async function createCodexCompressor(config, { log = () => {}, logError = () => {}, onActiveChild = () => {} } = {}) {
  // What makes a compressor configured here is having been told which model to
  // run, not having named the switch.
  const cfg = { ...config, recallCompressConfigured: Boolean(config.recallCompressModel || config.recallCompressThinking) };
  if (!["client", "auto"].includes(cfg.recallRewrite) || !isCodexAvailable()) return null;
  const cached = await loadCachedRecallCompressorProfile(cfg);
  const failedLongAgo = cached?.source === "runtime_failed" && !(Date.now() - Number(cached.failedAt) < RETRY_FAILED_MS);
  const profile = (!failedLongAgo && cached)
    || await detectRecallCompressorProfile(cfg, { log }).catch((error) => {
      logError("compress_profile_detect", error);
      return null;
    })
    || fallbackRecallCompressorProfile(cfg);
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

