/**
 * Shared configuration loader for the Claude Code OpenViking memory plugin.
 *
 * Resolution priority (highest → lowest):
 *   1. Environment variables (OPENVIKING_*)
 *   2. ovcli.conf (CLI client config: url, api_key, account, user, agent_id) — connection only
 *   3. ov.conf fields (server section + claude_code section) — legacy; new deployments should
 *      prefer env vars. Tuning fields under claude_code.* are still honored for backward compat.
 *   4. Built-in defaults
 *
 * Enable/disable:
 *   - OPENVIKING_MEMORY_ENABLED env var (0/false/no = off, 1/true/yes = on)
 *   - claude_code.enabled field in ov.conf (false = off)
 *   - Fallback: enabled when ov.conf or ovcli.conf exists, disabled otherwise
 *
 * Env vars covered (full list):
 *   Connection / identity:
 *     OPENVIKING_URL / OPENVIKING_BASE_URL, OPENVIKING_API_KEY / OPENVIKING_BEARER_TOKEN,
 *     OPENVIKING_ACCOUNT, OPENVIKING_USER, OPENVIKING_AGENT_ID
 *   Recall tuning:
 *     OPENVIKING_AUTO_RECALL, OPENVIKING_RECALL_LIMIT, OPENVIKING_RECALL_TOKEN_BUDGET,
 *     OPENVIKING_RECALL_MAX_CONTENT_CHARS, OPENVIKING_RECALL_PREFER_ABSTRACT,
 *     OPENVIKING_SCORE_THRESHOLD, OPENVIKING_MIN_QUERY_LENGTH, OPENVIKING_LOG_RANKING_DETAILS
 *   Capture tuning:
 *     OPENVIKING_AUTO_CAPTURE, OPENVIKING_CAPTURE_MODE, OPENVIKING_CAPTURE_MAX_LENGTH,
 *     OPENVIKING_CAPTURE_ASSISTANT_TURNS, OPENVIKING_COMMIT_TOKEN_THRESHOLD,
 *     OPENVIKING_RESUME_CONTEXT_BUDGET
 *   Lifecycle / behavior:
 *     OPENVIKING_TIMEOUT_MS, OPENVIKING_CAPTURE_TIMEOUT_MS, OPENVIKING_WRITE_PATH_ASYNC,
 *     OPENVIKING_BYPASS_SESSION, OPENVIKING_BYPASS_SESSION_PATTERNS (CSV)
 *   Misc:
 *     OPENVIKING_MEMORY_ENABLED, OPENVIKING_DEBUG, OPENVIKING_DEBUG_LOG,
 *     OPENVIKING_CONFIG_FILE, OPENVIKING_CLI_CONFIG_FILE
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
  // Accepts OPENVIKING_BEARER_TOKEN or OPENVIKING_API_KEY (sent as Bearer either way).
  const apiKey = str(process.env.OPENVIKING_BEARER_TOKEN, null)
    || str(process.env.OPENVIKING_API_KEY, null)
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

  // Each tuning field follows env > ovcli.conf is N/A (CLI doesn't carry tuning) >
  // ov.conf cc.* > built-in default. Env var names are flat OPENVIKING_* (no CC
  // namespace) to match the existing connection-field convention; they are only
  // read by this plugin's hooks.

  const debug = envBool("OPENVIKING_DEBUG") ?? (cc.debug === true);
  const defaultLogPath = join(homedir(), ".openviking", "logs", "cc-hooks.log");
  const debugLogPath = str(process.env.OPENVIKING_DEBUG_LOG, defaultLogPath);

  const timeoutMs = Math.max(1000, Math.floor(num(
    process.env.OPENVIKING_TIMEOUT_MS,
    num(cc.timeoutMs, 15000),
  )));
  const captureTimeoutMs = Math.max(1000, Math.floor(num(
    process.env.OPENVIKING_CAPTURE_TIMEOUT_MS,
    num(cc.captureTimeoutMs, Math.max(timeoutMs * 2, 30000)),
  )));

  // captureMode whitelist: env or cc, only "keyword" flips it; anything else → "semantic"
  const captureModeRaw = str(process.env.OPENVIKING_CAPTURE_MODE, str(cc.captureMode, "semantic"));
  const captureMode = captureModeRaw === "keyword" ? "keyword" : "semantic";

  // bypassSessionPatterns: env CSV overrides ov.conf array entirely
  const envPatterns = str(process.env.OPENVIKING_BYPASS_SESSION_PATTERNS, null);
  const bypassSessionPatterns = envPatterns
    ? envPatterns.split(",").map((s) => s.trim()).filter(Boolean)
    : (Array.isArray(cc.bypassSessionPatterns)
        ? cc.bypassSessionPatterns.filter((p) => typeof p === "string" && p.trim())
        : []);

  return {
    configPath,
    baseUrl,
    apiKey,
    agentId,
    accountId,
    userId,
    timeoutMs,

    // Recall
    autoRecall: envBool("OPENVIKING_AUTO_RECALL") ?? (cc.autoRecall !== false),
    recallLimit: Math.max(1, Math.floor(num(
      process.env.OPENVIKING_RECALL_LIMIT,
      num(cc.recallLimit, 6),
    ))),
    scoreThreshold: Math.min(1, Math.max(0, num(
      process.env.OPENVIKING_SCORE_THRESHOLD,
      num(cc.scoreThreshold, 0.35),
    ))),
    minQueryLength: Math.max(1, Math.floor(num(
      process.env.OPENVIKING_MIN_QUERY_LENGTH,
      num(cc.minQueryLength, 3),
    ))),
    logRankingDetails: envBool("OPENVIKING_LOG_RANKING_DETAILS") ?? (cc.logRankingDetails === true),
    // Ported from openclaw DEFAULT_RECALL_MAX_CONTENT_CHARS / DEFAULT_RECALL_TOKEN_BUDGET /
    // DEFAULT_RECALL_PREFER_ABSTRACT (openclaw-plugin/config.ts:44-47).
    recallMaxContentChars: Math.max(50, Math.floor(num(
      process.env.OPENVIKING_RECALL_MAX_CONTENT_CHARS,
      num(cc.recallMaxContentChars, 500),
    ))),
    recallTokenBudget: Math.max(200, Math.floor(num(
      process.env.OPENVIKING_RECALL_TOKEN_BUDGET,
      num(cc.recallTokenBudget, 2000),
    ))),
    recallPreferAbstract: envBool("OPENVIKING_RECALL_PREFER_ABSTRACT") ?? (cc.recallPreferAbstract !== false),

    // Capture
    autoCapture: envBool("OPENVIKING_AUTO_CAPTURE") ?? (cc.autoCapture !== false),
    captureMode,
    captureMaxLength: Math.max(200, Math.floor(num(
      process.env.OPENVIKING_CAPTURE_MAX_LENGTH,
      num(cc.captureMaxLength, 24000),
    ))),
    captureTimeoutMs,
    // Default true: a "memory plugin" without assistant-side capture only sees half the
    // conversation, which makes extraction noticeably worse. Subagent capture has always
    // pushed both sides (subagent-stop.mjs); this aligns the main-session path with that
    // behavior. Operators who want the old user-only behavior can still set
    // OPENVIKING_CAPTURE_ASSISTANT_TURNS=0 or claude_code.captureAssistantTurns=false.
    captureAssistantTurns: envBool("OPENVIKING_CAPTURE_ASSISTANT_TURNS") ?? (cc.captureAssistantTurns !== false),
    // P0-2: client-driven commit threshold (ported from openclaw afterTurn).
    // Default 20000 aligns with openclaw; lower values produce archives faster.
    commitTokenThreshold: Math.max(1000, Math.floor(num(
      process.env.OPENVIKING_COMMIT_TOKEN_THRESHOLD,
      num(cc.commitTokenThreshold, 20000),
    ))),

    // P0-3b: token budget for session-start archive-overview fetch
    resumeContextBudget: Math.max(1024, Math.floor(num(
      process.env.OPENVIKING_RESUME_CONTEXT_BUDGET,
      num(cc.resumeContextBudget, 32000),
    ))),

    // P1-15: bypass patterns (glob) — when the CC session_id or cwd matches,
    // skip capture/recall entirely. Useful for one-off scratch sessions that
    // should not contaminate OV.
    bypassSessionPatterns,
    bypassSession: envBool("OPENVIKING_BYPASS_SESSION") ?? false,

    // Write-path async: auto-capture / session-end / subagent-stop fire-and-
    // forget via a detached child process, so the hook returns to CC instantly.
    // pre-compact stays sync regardless (CC rewrites transcript right after).
    // Default on — OV commit is already half-async server-side, so eventual
    // consistency matches the sync path.
    writePathAsync: envBool("OPENVIKING_WRITE_PATH_ASYNC") ?? (cc.writePathAsync !== false),

    // Debug
    debug,
    debugLogPath,
  };
}
