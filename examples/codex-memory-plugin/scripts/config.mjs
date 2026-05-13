/**
 * Shared configuration loader for the Codex OpenViking memory plugin.
 *
 * Resolution priority (highest → lowest), per-field:
 *   1. Environment variables (OPENVIKING_*)
 *   2. ovcli.conf — the CLI client config (carries url/api_key/account/user/agent_id)
 *   3. ov.conf — the server config (server.* + optional codex.* block for tuning)
 *   4. Built-in defaults
 *
 * Mirrors examples/claude-code-memory-plugin/scripts/config.mjs so the
 * hook surface and the MCP server (src/memory-server.ts imports loadConfig
 * from here) resolve identity identically. Aligning the two prevents
 * silent identity drift between auto-capture and explicit `remember` calls.
 *
 * File-path env vars:
 *   OPENVIKING_CLI_CONFIG_FILE  alternate ovcli.conf path  (preferred)
 *   OPENVIKING_CONFIG_FILE      alternate ov.conf path
 *
 * For backward compat, if only OPENVIKING_CONFIG_FILE is set and the file
 * it points at parses as an ovcli.conf (top-level `url`/`api_key`, no
 * `server` section), it is treated as ovcli.conf — earlier versions of
 * this plugin used OPENVIKING_CONFIG_FILE to mean either file.
 *
 * Connection / identity env vars:
 *   OPENVIKING_URL / OPENVIKING_BASE_URL
 *   OPENVIKING_API_KEY / OPENVIKING_BEARER_TOKEN
 *   OPENVIKING_ACCOUNT, OPENVIKING_USER, OPENVIKING_AGENT_ID
 *
 * Misc env vars:
 *   OPENVIKING_TIMEOUT_MS, OPENVIKING_CAPTURE_TIMEOUT_MS
 *   OPENVIKING_RECALL_LIMIT, OPENVIKING_SCORE_THRESHOLD
 *   OPENVIKING_DEBUG=1, OPENVIKING_DEBUG_LOG
 */

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";

const DEFAULT_OVCLI_CONF_PATH = join(homedir(), ".openviking", "ovcli.conf");
const DEFAULT_OV_CONF_PATH = join(homedir(), ".openviking", "ov.conf");

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

function tryLoadJson(path) {
  let raw;
  try {
    raw = readFileSync(path, "utf-8");
  } catch {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch {
    process.stderr.write(`[openviking-memory] Invalid config file: ${path}\n`);
    return null;
  }
}

function looksLikeOvcli(obj) {
  if (!obj || typeof obj !== "object") return false;
  if (obj.server && typeof obj.server === "object") return false;
  return typeof obj.url === "string" || typeof obj.api_key === "string";
}

/**
 * Returns { cliFile, cliPath, ovFile, ovPath }. Missing files are
 * represented as empty objects, so callers can read fields unconditionally.
 *
 * OPENVIKING_CLI_CONFIG_FILE overrides the ovcli.conf path.
 * OPENVIKING_CONFIG_FILE overrides the ov.conf path; if the file looks
 * like an ovcli.conf (no `server` section + has `url`/`api_key`), it is
 * also used as the cliFile to support the legacy "pass any conf via
 * OPENVIKING_CONFIG_FILE" pattern.
 */
function loadFiles() {
  const cliPathEnv = process.env.OPENVIKING_CLI_CONFIG_FILE
    ? resolvePath(process.env.OPENVIKING_CLI_CONFIG_FILE.replace(/^~/, homedir()))
    : null;
  const ovPathEnv = process.env.OPENVIKING_CONFIG_FILE
    ? resolvePath(process.env.OPENVIKING_CONFIG_FILE.replace(/^~/, homedir()))
    : null;

  const cliPath = cliPathEnv || DEFAULT_OVCLI_CONF_PATH;
  const ovPath = ovPathEnv || DEFAULT_OV_CONF_PATH;

  let cliFile = tryLoadJson(cliPath);
  let cliLoadedFrom = cliFile ? cliPath : null;
  let ovFile = tryLoadJson(ovPath);
  let ovLoadedFrom = ovFile ? ovPath : null;

  // Backward compat: OPENVIKING_CONFIG_FILE pointing at an ovcli-shaped file.
  // Earlier plugin versions had a single OPENVIKING_CONFIG_FILE that could
  // point at either ov.conf or ovcli.conf; preserve that by promoting.
  if (ovPathEnv && !cliPathEnv && looksLikeOvcli(ovFile)) {
    cliFile = ovFile;
    cliLoadedFrom = ovLoadedFrom;
    ovFile = null;
    ovLoadedFrom = null;
  }

  return {
    cliFile: cliFile || {},
    cliPath: cliLoadedFrom,
    ovFile: ovFile || {},
    ovPath: ovLoadedFrom,
  };
}

function deriveBaseUrl({ cliFile, ovFile }) {
  const envUrl = str(process.env.OPENVIKING_URL, null) || str(process.env.OPENVIKING_BASE_URL, null);
  if (envUrl) return envUrl.replace(/\/+$/, "");

  const cliUrl = str(cliFile.url, null);
  if (cliUrl) return cliUrl.replace(/\/+$/, "");

  const server = ovFile.server || {};
  const ovUrl = str(server.url, null);
  if (ovUrl) return ovUrl.replace(/\/+$/, "");

  const host = str(server.host, "127.0.0.1").replace("0.0.0.0", "127.0.0.1");
  const port = Math.floor(num(server.port, 1933));
  return `http://${host}:${port}`;
}

export function loadConfig() {
  const { cliFile, cliPath, ovFile, ovPath } = loadFiles();
  const configPath = cliPath || ovPath || null;

  const server = ovFile.server || {};
  const cx = ovFile.codex || {};

  const baseUrl = deriveBaseUrl({ cliFile, ovFile });

  // apiKey: env > cliFile.api_key > codex.apiKey > server.root_api_key
  // Accepts OPENVIKING_BEARER_TOKEN or OPENVIKING_API_KEY (sent as Bearer either way).
  const apiKey =
    str(process.env.OPENVIKING_BEARER_TOKEN, null) ||
    str(process.env.OPENVIKING_API_KEY, null) ||
    str(cliFile.api_key, null) ||
    str(cx.apiKey, null) ||
    str(server.root_api_key, "");

  // account: env > cliFile.account > codex.accountId > ""
  const account =
    str(process.env.OPENVIKING_ACCOUNT, null) ||
    str(cliFile.account, null) ||
    str(cx.accountId, "");

  // user: env > cliFile.user > codex.userId > ""
  const user =
    str(process.env.OPENVIKING_USER, null) ||
    str(cliFile.user, null) ||
    str(cx.userId, "");

  // agentId: env > cliFile.agent_id > codex.agentId > "codex"
  const agentId =
    str(process.env.OPENVIKING_AGENT_ID, null) ||
    str(cliFile.agent_id, null) ||
    str(cx.agentId, "codex");

  const debug = envBool("OPENVIKING_DEBUG") ?? (cx.debug === true);
  const defaultLogPath = join(homedir(), ".openviking", "logs", "codex-hooks.log");
  const debugLogPath = str(process.env.OPENVIKING_DEBUG_LOG, defaultLogPath);

  const timeoutMs = Math.max(1000, Math.floor(num(
    process.env.OPENVIKING_TIMEOUT_MS,
    num(cx.timeoutMs, 15000),
  )));
  const captureTimeoutMs = Math.max(1000, Math.floor(num(
    process.env.OPENVIKING_CAPTURE_TIMEOUT_MS,
    num(cx.captureTimeoutMs, Math.max(timeoutMs * 2, 30000)),
  )));

  return {
    configPath,
    cliConfigPath: cliPath,
    ovConfigPath: ovPath,
    baseUrl,
    apiKey,
    account,
    user,
    agentId,
    timeoutMs,

    autoRecall: envBool("OPENVIKING_AUTO_RECALL") ?? (cx.autoRecall !== false),
    recallLimit: Math.max(1, Math.floor(num(
      process.env.OPENVIKING_RECALL_LIMIT,
      num(cx.recallLimit, 6),
    ))),
    scoreThreshold: Math.min(1, Math.max(0, num(
      process.env.OPENVIKING_SCORE_THRESHOLD,
      num(cx.scoreThreshold, 0.01),
    ))),
    minQueryLength: Math.max(1, Math.floor(num(
      process.env.OPENVIKING_MIN_QUERY_LENGTH,
      num(cx.minQueryLength, 3),
    ))),
    logRankingDetails: envBool("OPENVIKING_LOG_RANKING_DETAILS") ?? (cx.logRankingDetails === true),

    autoCapture: envBool("OPENVIKING_AUTO_CAPTURE") ?? (cx.autoCapture !== false),
    captureMode: (str(process.env.OPENVIKING_CAPTURE_MODE, str(cx.captureMode, "semantic")) === "keyword")
      ? "keyword"
      : "semantic",
    captureMaxLength: Math.max(200, Math.floor(num(
      process.env.OPENVIKING_CAPTURE_MAX_LENGTH,
      num(cx.captureMaxLength, 24000),
    ))),
    captureTimeoutMs,
    captureAssistantTurns: envBool("OPENVIKING_CAPTURE_ASSISTANT_TURNS") ?? (cx.captureAssistantTurns === true),
    captureLastAssistantOnStop: envBool("OPENVIKING_CAPTURE_LAST_ASSISTANT_ON_STOP") ?? (cx.captureLastAssistantOnStop !== false),

    autoCommitOnCompact: envBool("OPENVIKING_AUTO_COMMIT_ON_COMPACT") ?? (cx.autoCommitOnCompact !== false),

    debug,
    debugLogPath,
  };
}
