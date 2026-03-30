/**
 * Shared configuration loader for the Claude Code OpenViking memory plugin.
 *
 * Reads from the OpenViking server config file (ov.conf, JSON format),
 * shared with the OpenClaw plugin and other OpenViking clients.
 *
 * Env var: OPENVIKING_CONFIG_FILE (default: ~/.openviking/ov.conf)
 *
 * Connection info is derived from ov.conf's `server` section.
 * Plugin-specific overrides go in an optional `claude_code` section.
 */

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";

const DEFAULT_CONFIG_PATH = join(homedir(), ".openviking", "ov.conf");

function num(val, fallback) {
  if (typeof val === "number" && Number.isFinite(val)) return val;
  if (typeof val === "string" && val.trim()) {
    const n = Number(val);
    if (Number.isFinite(n)) return n;
  }
  return fallback;
}

function str(val, fallback) {
  if (typeof val === "string" && val.trim()) return val.trim();
  return fallback;
}

export function loadConfig() {
  const configPath = resolvePath(
    (process.env.OPENVIKING_CONFIG_FILE || DEFAULT_CONFIG_PATH).replace(/^~/, homedir()),
  );

  let raw;
  try {
    raw = readFileSync(configPath, "utf-8");
  } catch (err) {
    const msg = err?.code === "ENOENT"
      ? `Config file not found: ${configPath}\n  Create it from the example: cp ov.conf.example ~/.openviking/ov.conf`
      : `Failed to read config file: ${configPath} — ${err?.message || err}`;
    process.stderr.write(`[openviking-memory] ${msg}\n`);
    process.exit(1);
  }

  let file;
  try {
    file = JSON.parse(raw);
  } catch (err) {
    process.stderr.write(`[openviking-memory] Invalid JSON in ${configPath}: ${err?.message || err}\n`);
    process.exit(1);
  }

  // Server connection — from ov.conf [server] section
  const server = file.server || {};
  const host = str(server.host, "127.0.0.1").replace("0.0.0.0", "127.0.0.1");
  const port = Math.floor(num(server.port, 1933));
  const baseUrl = `http://${host}:${port}`;
  const apiKey = str(server.root_api_key, "") || "";

  // Plugin-specific overrides — from optional [claude_code] section
  const cc = file.claude_code || {};

  const debug = cc.debug === true || process.env.OPENVIKING_DEBUG === "1";
  const defaultLogPath = join(homedir(), ".openviking", "logs", "cc-hooks.log");
  const debugLogPath = str(process.env.OPENVIKING_DEBUG_LOG, defaultLogPath);

  const timeoutMs = Math.max(1000, Math.floor(num(cc.timeoutMs, 15000)));
  const captureTimeoutMs = Math.max(
    1000,
    Math.floor(num(cc.captureTimeoutMs, Math.max(timeoutMs * 2, 30000))),
  );

  return {
    configPath,
    baseUrl,
    apiKey,
    agentId: str(cc.agentId, "claude-code"),
    timeoutMs,

    // Recall
    autoRecall: cc.autoRecall !== false,
    recallLimit: Math.max(1, Math.floor(num(cc.recallLimit, 6))),
    scoreThreshold: Math.min(1, Math.max(0, num(cc.scoreThreshold, 0.01))),
    minQueryLength: Math.max(1, Math.floor(num(cc.minQueryLength, 3))),
    logRankingDetails: cc.logRankingDetails === true,

    // Capture
    autoCapture: cc.autoCapture !== false,
    captureMode: cc.captureMode === "keyword" ? "keyword" : "semantic",
    captureMaxLength: Math.max(200, Math.floor(num(cc.captureMaxLength, 24000))),
    captureTimeoutMs,
    captureAssistantTurns: cc.captureAssistantTurns === true,

    // Debug
    debug,
    debugLogPath,
  };
}
