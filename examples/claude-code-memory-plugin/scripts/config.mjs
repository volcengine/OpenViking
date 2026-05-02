/**
 * Shared configuration loader for the Claude Code OpenViking memory plugin.
 *
 * Resolution priority (highest → lowest):
 *   1. Environment variables (OPENVIKING_URL, OPENVIKING_API_KEY, etc.)
 *   2. ovcli.conf (CLI client config: url, api_key, account, user, agent_id)
 *   3. ov.conf fields (server section + claude_code section)
 *   4. Built-in defaults
 *
 * Enable/disable:
 *   - OPENVIKING_MEMORY_ENABLED env var (0/false/no = off, 1/true/yes = on)
 *   - claude_code.enabled field in ov.conf (false = off)
 *   - Fallback: enabled when ov.conf or ovcli.conf exists, disabled otherwise
 *
 * Config file env vars:
 *   - OPENVIKING_CONFIG_FILE      → ov.conf path     (default: ~/.openviking/ov.conf)
 *   - OPENVIKING_CLI_CONFIG_FILE  → ovcli.conf path  (default: ~/.openviking/ovcli.conf)
 */

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";

const DEFAULT_OV_CONF_PATH = join(homedir(), ".openviking", "ov.conf");
const DEFAULT_OVCLI_CONF_PATH = join(homedir(), ".openviking", "ovcli.conf");

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

function envBool(name) {
  const v = process.env[name];
  if (v == null || v === "") return undefined;
  const lower = v.trim().toLowerCase();
  if (lower === "0" || lower === "false" || lower === "no") return false;
  if (lower === "1" || lower === "true" || lower === "yes") return true;
  return undefined;
}

/**
 * Try to load and parse a JSON config file. Returns parsed object or null.
 */
function tryLoadJsonFile(envVar, defaultPath) {
  const configPath = resolvePath(
    (process.env[envVar] || defaultPath).replace(/^~/, homedir()),
  );

  let raw;
  try {
    raw = readFileSync(configPath, "utf-8");
  } catch {
    return null;
  }

  try {
    return { configPath, file: JSON.parse(raw) };
  } catch {
    return null;
  }
}

/**
 * Determine whether the plugin is enabled.
 *
 * Priority:
 *   1. OPENVIKING_MEMORY_ENABLED env var
 *   2. claude_code.enabled in ov.conf
 *   3. Whether ov.conf or ovcli.conf exists and is parseable
 *
 * When force-enabled via env var (=1) without config files, the caller must
 * provide connection info via other env vars (OPENVIKING_URL, etc.).
 */
export function isPluginEnabled() {
  const envEnabled = envBool("OPENVIKING_MEMORY_ENABLED");
  if (envEnabled !== undefined) return envEnabled;

  const ovConf = tryLoadJsonFile("OPENVIKING_CONFIG_FILE", DEFAULT_OV_CONF_PATH);
  if (ovConf) {
    const cc = ovConf.file.claude_code || {};
    if (cc.enabled === false) return false;
    return true;
  }

  // No ov.conf — check if ovcli.conf exists (sufficient for connection info)
  const cliConf = tryLoadJsonFile("OPENVIKING_CLI_CONFIG_FILE", DEFAULT_OVCLI_CONF_PATH);
  if (cliConf) return true;

  return false;
}

/**
 * Load the full plugin configuration.
 *
 * Resolution: env vars → ovcli.conf → ov.conf → defaults.
 */
export function loadConfig() {
  const ovConf = tryLoadJsonFile("OPENVIKING_CONFIG_FILE", DEFAULT_OV_CONF_PATH);
  const cliConf = tryLoadJsonFile("OPENVIKING_CLI_CONFIG_FILE", DEFAULT_OVCLI_CONF_PATH);

  const ovFile = ovConf?.file || {};
  const cliFile = cliConf?.file || {};
  const configPath = ovConf?.configPath || cliConf?.configPath || null;

  const server = ovFile.server || {};
  const cc = ovFile.claude_code || {};

  // baseUrl: env → ovcli.url → ov.server.url → http://{host}:{port}
  const envUrl = str(process.env.OPENVIKING_URL, null) || str(process.env.OPENVIKING_BASE_URL, null);
  let baseUrl;
  if (envUrl) {
    baseUrl = envUrl.replace(/\/+$/, "");
  } else if (cliFile.url) {
    baseUrl = str(cliFile.url, "").replace(/\/+$/, "");
  } else if (server.url) {
    baseUrl = str(server.url, "").replace(/\/+$/, "");
  } else {
    const host = str(server.host, "127.0.0.1").replace("0.0.0.0", "127.0.0.1");
    const port = Math.floor(num(server.port, 1933));
    baseUrl = `http://${host}:${port}`;
  }

  // apiKey: env → ovcli.api_key → cc.apiKey → server.root_api_key
  const apiKey = str(process.env.OPENVIKING_API_KEY, null)
    || str(cliFile.api_key, null)
    || str(cc.apiKey, null)
    || str(server.root_api_key, "");

  // agentId: env → ovcli.agent_id → cc.agentId → "claude-code"
  const agentId = str(process.env.OPENVIKING_AGENT_ID, null)
    || str(cliFile.agent_id, null)
    || str(cc.agentId, "claude-code");

  // accountId: env → ovcli.account → cc.accountId → ""
  const accountId = str(process.env.OPENVIKING_ACCOUNT, null)
    || str(cliFile.account, null)
    || str(cc.accountId, "");

  // userId: env → ovcli.user → cc.userId → ""
  const userId = str(process.env.OPENVIKING_USER, null)
    || str(cliFile.user, null)
    || str(cc.userId, "");

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
    agentId,
    accountId,
    userId,
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
