/**
 * Shared configuration loader for the Claude Code OpenViking context engine.
 * Reads from ~/.openviking/ov.conf (JSON), shared with OpenClaw and other clients.
 * Plugin-specific overrides in optional "claude_code" section.
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
    process.stderr.write(`[openviking-context-engine] ${msg}\n`);
    process.exit(1);
  }

  let file;
  try {
    file = JSON.parse(raw);
  } catch (err) {
    process.stderr.write(`[openviking-context-engine] Invalid JSON in ${configPath}: ${err?.message || err}\n`);
    process.exit(1);
  }

  const server = file.server || {};
  const host = str(server.host, "127.0.0.1").replace("0.0.0.0", "127.0.0.1");
  const port = Math.floor(num(server.port, 1933));
  const baseUrl = `http://${host}:${port}`;
  const apiKey = str(server.root_api_key, "") || "";
  const account = str(server.account, "default");
  const user = str(server.user, "default");

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
    account,
    user,
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
    captureAssistantTurns: cc.captureAssistantTurns !== false, // default true for context engine

    // Compact thresholds
    commitTokenThreshold: Math.max(0, Math.floor(num(cc.commitTokenThreshold, 20000))),
    commitTurnThreshold: Math.max(1, Math.floor(num(cc.commitTurnThreshold, 20))),
    commitIntervalMs: Math.max(60000, Math.floor(num(cc.commitIntervalMs, 30 * 60 * 1000))),
    commitTokensAddedThreshold: Math.max(1000, Math.floor(num(cc.commitTokensAddedThreshold, 30000))),
    contextTokenBudget: Math.max(1000, Math.floor(num(cc.contextTokenBudget, 128000))),
    recallTokenBudget: Math.max(100, Math.floor(num(cc.recallTokenBudget, 2000))),
    recallMaxContentChars: Math.max(50, Math.floor(num(cc.recallMaxContentChars, 500))),

    // Debug
    debug,
    debugLogPath,
  };
}
