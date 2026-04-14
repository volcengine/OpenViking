/**
 * Shared configuration loader for the Claude Code OpenViking memory plugin.
 *
 * Reads client config from:
 *   1. OPENVIKING_CC_CONFIG_FILE
 *   2. ~/.openviking/claude-code-memory-plugin/config.json
 *
 * In local mode, apiKey defaults to the local OpenViking server config:
 *   1. OPENVIKING_CONFIG_FILE
 *   2. ~/.openviking/ov.conf
 */

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";

export const DEFAULT_CLIENT_CONFIG_PATH = join(
  homedir(),
  ".openviking",
  "claude-code-memory-plugin",
  "config.json",
);
export const DEFAULT_SERVER_CONFIG_PATH = join(homedir(), ".openviking", "ov.conf");

function fatal(message) {
  process.stderr.write(`[openviking-memory] ${message}\n`);
  process.exit(1);
}

function num(val, fallback) {
  if (typeof val === "number" && Number.isFinite(val)) return val;
  if (typeof val === "string" && val.trim()) {
    const n = Number(val);
    if (Number.isFinite(n)) return n;
  }
  return fallback;
}

function resolveEnvVars(value) {
  return value.replace(/\$\{([^}]+)\}/g, (_, envVar) => {
    const envValue = process.env[envVar];
    if (typeof envValue !== "string" || envValue === "") {
      fatal(`Environment variable ${envVar} is not set`);
    }
    return envValue;
  });
}

function str(val, fallback) {
  if (typeof val === "string" && val.trim()) return resolveEnvVars(val.trim());
  return fallback;
}

function bool(val, fallback = false) {
  return typeof val === "boolean" ? val : fallback;
}

function resolveConfigPath(rawValue, fallback) {
  return resolvePath(str(rawValue, fallback).replace(/^~/, homedir()));
}

function normalizeBaseUrl(value) {
  return str(value, "").replace(/\/+$/, "");
}

function requireBaseUrl(value) {
  const resolved = normalizeBaseUrl(value);
  if (!resolved) {
    fatal("Claude Code client config: baseUrl is required when mode is \"remote\"");
  }
  return resolved;
}

function clampPort(value) {
  return Math.max(1, Math.min(65535, Math.floor(num(value, 1933))));
}

function readJsonFileStrict(configPath, label) {
  let raw;
  try {
    raw = readFileSync(configPath, "utf-8");
  } catch (err) {
    const msg = err?.code === "ENOENT"
      ? `${label} not found: ${configPath}
  Create it and set at least: { "mode": "local" }`
      : `Failed to read ${label}: ${configPath} — ${err?.message || err}`;
    fatal(msg);
  }

  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      fatal(`${label} must contain a JSON object: ${configPath}`);
    }
    return parsed;
  } catch (err) {
    fatal(`Invalid JSON in ${configPath}: ${err?.message || err}`);
  }
}

function readJsonFileOptional(configPath) {
  try {
    const parsed = JSON.parse(readFileSync(configPath, "utf-8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { file: null, error: `JSON root must be an object: ${configPath}` };
    }
    return { file: parsed, error: null };
  } catch (err) {
    if (err?.code === "ENOENT") return { file: null, error: null };
    return { file: null, error: err?.message || String(err) };
  }
}

export function loadConfig() {
  const configPath = resolveConfigPath(
    process.env.OPENVIKING_CC_CONFIG_FILE,
    DEFAULT_CLIENT_CONFIG_PATH,
  );
  const file = readJsonFileStrict(configPath, "Claude Code client config");
  const mode = file.mode === "remote" ? "remote" : "local";

  const serverConfigPath = resolveConfigPath(
    process.env.OPENVIKING_CONFIG_FILE,
    DEFAULT_SERVER_CONFIG_PATH,
  );
  const serverConfigResult = mode === "local"
    ? readJsonFileOptional(serverConfigPath)
    : { file: null, error: null };
  const server = serverConfigResult.file?.server || {};

  const timeoutMs = Math.max(1000, Math.floor(num(file.timeoutMs, 15000)));
  const captureTimeoutMs = Math.max(
    1000,
    Math.floor(num(file.captureTimeoutMs, Math.max(timeoutMs * 2, 30000))),
  );
  const debug = bool(file.debug) || process.env.OPENVIKING_DEBUG === "1";
  const defaultLogPath = join(homedir(), ".openviking", "logs", "cc-hooks.log");
  const debugLogPath = resolveConfigPath(
    process.env.OPENVIKING_DEBUG_LOG ?? file.debugLogPath,
    defaultLogPath,
  );
  const localApiKey = str(server.root_api_key, "");
  const configuredApiKey = str(file.apiKey, "");
  const baseUrl = mode === "remote"
    ? requireBaseUrl(file.baseUrl)
    : `http://127.0.0.1:${clampPort(server.port)}`;

  return {
    mode,
    configPath,
    serverConfigPath,
    serverConfigError: serverConfigResult.error,
    baseUrl,
    apiKey: configuredApiKey || (mode === "local" ? localApiKey : ""),
    agentId: str(file.agentId, "claude-code"),
    account: str(file.account, ""),
    user: str(file.user, ""),
    timeoutMs,

    // Recall
    autoRecall: file.autoRecall !== false,
    recallLimit: Math.max(1, Math.floor(num(file.recallLimit, 6))),
    scoreThreshold: Math.min(1, Math.max(0, num(file.scoreThreshold, 0.01))),
    minQueryLength: Math.max(1, Math.floor(num(file.minQueryLength, 3))),
    logRankingDetails: bool(file.logRankingDetails),

    // Capture
    autoCapture: file.autoCapture !== false,
    captureMode: file.captureMode === "keyword" ? "keyword" : "semantic",
    captureMaxLength: Math.max(200, Math.floor(num(file.captureMaxLength, 24000))),
    captureTimeoutMs,
    captureAssistantTurns: bool(file.captureAssistantTurns),

    // Debug
    debug,
    debugLogPath,
  };
}
