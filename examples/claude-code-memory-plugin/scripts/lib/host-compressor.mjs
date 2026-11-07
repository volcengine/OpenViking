/**
 * Local digest compression through the host CLI.
 *
 * Running the rewrite here keeps the token cost on the user's own subscription
 * instead of the OpenViking deployment. The default model/effort pair is
 * Haiku + low, which keeps the reasoning budget, and thus latency, clamped.
 *
 * Everything here is best-effort: a missing CLI, a timeout, or malformed output
 * means no digest, and the caller injects the unrewritten context block.
 */

import { spawn } from "node:child_process";
import { readFile, mkdir, rename, rm, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

const PROBE_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const PROBE_TIMEOUT_MS = 4000;
const BLOCK_TTL_MS = 7 * 24 * 60 * 60 * 1000;

function statePath(name) {
  return join(homedir(), ".openviking", "state", name);
}

async function readJson(path) {
  try { return JSON.parse(await readFile(path, "utf8")); } catch { return null; }
}

async function writeJson(path, value) {
  try {
    await mkdir(dirname(path), { recursive: true });
    const tmp = `${path}.tmp`;
    await writeFile(tmp, JSON.stringify(value));
    await rename(tmp, path);
  } catch { /* best effort */ }
}

/**
 * Child processes must not re-enter the plugin: every OV hook is disabled and
 * MCP config is emptied, so the compressor cannot recurse or fan out tools.
 */
function childEnv() {
  return {
    ...process.env,
    OPENVIKING_MEMORY_ENABLED: "0",
    OPENVIKING_AUTO_RECALL: "0",
    OPENVIKING_AUTO_CAPTURE: "0",
    OPENVIKING_NO_AUTO_INJECT: "1",
    OPENVIKING_RECALL_COMPRESS: "off",
    OPENVIKING_RECALL_REWRITE: "off",
  };
}

function runCommand(command, args, { timeoutMs, input = "" }) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(command, args, { env: childEnv(), stdio: ["pipe", "pipe", "pipe"] });
    } catch {
      resolve({ ok: false, stdout: "", code: -1 });
      return;
    }

    let stdout = "";
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(result);
    };
    const timer = setTimeout(() => {
      try { child.kill("SIGKILL"); } catch { /* already gone */ }
      finish({ ok: false, stdout, code: -1, timedOut: true });
    }, Math.max(1000, timeoutMs));

    child.stdout.on("data", (chunk) => { stdout += String(chunk); });
    child.stderr.on("data", () => {});
    child.on("error", () => finish({ ok: false, stdout, code: -1 }));
    child.on("close", (code) => finish({ ok: code === 0, stdout, code }));

    if (input) {
      child.stdin.end(input);
    } else {
      child.stdin.end();
    }
  });
}

/**
 * Whether the host CLI is present and runnable. Cached for a week so the probe
 * does not tax every turn.
 */
export async function probeHostCli(command = "claude", cachePath = statePath("host-cli-probe.json"), now = Date.now()) {
  const cached = await readJson(cachePath);
  if (cached && cached.command === command && Number(cached.checkedAt) + PROBE_TTL_MS > now) {
    return Boolean(cached.available);
  }
  const result = await runCommand(command, ["--version"], { timeoutMs: PROBE_TIMEOUT_MS });
  await writeJson(cachePath, { command, available: result.ok, checkedAt: now });
  return result.ok;
}

/**
 * Whether a previous run of this configuration already failed.
 *
 * A hook is its own process, so remembering the failure in memory would not
 * survive to the next prompt: every turn would spawn the same doomed child and
 * pay the whole timeout before falling back to the block it could have used
 * immediately. The record is therefore a file, keyed to the configuration that
 * produced it, so changing the model or the effort earns a fresh attempt, and
 * it expires on its own after `recallCompressDetectTtlMs`.
 */
function blockPath() {
  return statePath("host-compressor-block.json");
}

function blockKey({ command, model, effort, mode }) {
  return JSON.stringify({ command, model, effort, mode });
}

export async function loadHostCompressorBlock(cfg = {}, key) {
  const cached = await readJson(blockPath());
  if (!cached || cached.configKey !== key) return null;
  const ttl = Number(cfg.recallCompressDetectTtlMs ?? BLOCK_TTL_MS);
  if (ttl > 0 && Date.now() - Number(cached.checkedAt || 0) > ttl) return null;
  return cached;
}

export async function markHostCompressorBlocked(key, model = "") {
  await writeJson(blockPath(), { configKey: key, failedModel: model, checkedAt: Date.now() });
}

/** Forget the record so the next SessionStart re-tries the compressor once. */
export async function clearHostCompressorBlock() {
  try { await rm(blockPath(), { force: true }); } catch { /* best effort */ }
}

/**
 * Build the `runCompressor(prompt) -> text` callback the shared recall core
 * expects, or null when no local compressor is available.
 */
export async function createHostCompressor(cfg = {}, log = () => {}) {
  const mode = String(cfg.recallRewrite || "off").toLowerCase();
  if (mode !== "client" && mode !== "auto") return null;

  const command = String(cfg.recallCompressCommand || "claude");
  if (!(await probeHostCli(command))) {
    log("host_compressor_unavailable", { command });
    return null;
  }

  const model = String(cfg.recallCompressModel || "haiku");
  const effort = String(cfg.recallCompressEffort || "low");
  const timeoutMs = Math.max(5000, Number(cfg.recallCompressTimeoutMs || 30000));
  const key = blockKey({ command, model, effort, mode });

  const blocked = await loadHostCompressorBlock(cfg, key);
  if (blocked) {
    log("host_compressor_blocked", {
      model,
      failedModel: blocked.failedModel || model,
      since: Number(blocked.checkedAt) || 0,
    });
    return null;
  }

  // One hook can ask for two digests (a legacy-peer pass falls back to a second
  // search). After the first failure the second would wait out the same timeout
  // for the same answer, so this stops at one.
  let available = true;

  return async (prompt) => {
    if (!available) return "";
    const args = [
      "-p",
      "--model", model,
      "--effort", effort,
      "--strict-mcp-config",
      "--mcp-config", JSON.stringify({ mcpServers: {} }),
    ];
    const result = await runCommand(command, args, { timeoutMs, input: prompt });
    if (!result.ok) {
      available = false;
      log("host_compressor_failed", { code: result.code, timedOut: Boolean(result.timedOut), model });
      // Fire and forget: a hook that waits on its own audit trail can still miss
      // the deadline it is trying to stay inside.
      void markHostCompressorBlocked(key, model).catch(() => {});
      return "";
    }
    return result.stdout;
  };
}
