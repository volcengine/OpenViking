/**
 * Local digest compression through the host CLI.
 *
 * Running the rewrite here keeps the token cost on the user's own subscription
 * instead of the OpenViking deployment. Measured against a server-side rewrite
 * the latency is the same order as long as the reasoning budget is actually
 * clamped, which is why the default model/effort pair is Sonnet + low (Haiku
 * ignores the effort knob and its latency is unbounded).
 *
 * Everything here is best-effort: a missing CLI, a timeout, or malformed output
 * means no digest, and the caller injects the unrewritten context block.
 */

import { spawn } from "node:child_process";
import { readFile, mkdir, rename, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

const PROBE_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const PROBE_TIMEOUT_MS = 4000;

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

  const model = String(cfg.recallCompressModel || "sonnet");
  const effort = String(cfg.recallCompressEffort || "low");
  const timeoutMs = Math.max(5000, Number(cfg.recallCompressTimeoutMs || 30000));

  return async (prompt) => {
    const args = [
      "-p",
      "--model", model,
      "--effort", effort,
      "--strict-mcp-config",
      "--mcp-config", JSON.stringify({ mcpServers: {} }),
    ];
    const result = await runCommand(command, args, { timeoutMs, input: prompt });
    if (!result.ok) {
      log("host_compressor_failed", { code: result.code, timedOut: Boolean(result.timedOut) });
      return "";
    }
    return result.stdout;
  };
}
