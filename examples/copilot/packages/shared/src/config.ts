/**
 * Shared configuration loader for the OpenViking Copilot plugins
 * (VS Code extension + Copilot CLI MCP server).
 *
 * Resolution priority (highest -> lowest):
 *   1. hostOverrides (e.g. VS Code workspace/user settings — only the host
 *      knows about these, so they're injected by the caller)
 *   2. Environment variables (OPENVIKING_*)
 *   3. ovcli.conf (CLI client config: url, api_key, account, user, agent_id)
 *      — connection only
 *   4. ov.conf (server section + copilot block, with claude_code as legacy
 *      fallback for tuning fields)
 *   5. Built-in defaults
 *
 * Enable/disable:
 *   - OPENVIKING_MEMORY_ENABLED env var (0/false/no = off, 1/true/yes = on)
 *   - copilot.enabled / claude_code.enabled in ov.conf (false = off)
 *   - Fallback: enabled when ov.conf or ovcli.conf exists, disabled otherwise
 */

import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";
import { envBool, num, str } from "./util/env.js";

const DEFAULT_OV_CONF_PATH = join(homedir(), ".openviking", "ov.conf");
const DEFAULT_OVCLI_CONF_PATH = join(homedir(), ".openviking", "ovcli.conf");
const DEFAULT_LOG_DIR = join(homedir(), ".openviking", "logs");

export type AgentIdDefault = "copilot-vscode" | "copilot-cli";
export type CaptureMode = "semantic" | "keyword";

export interface PluginConfig {
  /** Path to the config file the loader resolved (ov.conf preferred), or null. */
  configPath: string | null;
  /** OpenViking server base URL (no trailing slash). */
  baseUrl: string;
  /** Bearer token. Empty string when unauthenticated (local mode). */
  apiKey: string;
  /** Agent identity, sent as X-OpenViking-Agent. */
  agentId: string;
  /** Tenant account, sent as X-OpenViking-Account. */
  accountId: string;
  /** Tenant user, sent as X-OpenViking-User. */
  userId: string;
  /** General HTTP timeout (ms). */
  timeoutMs: number;

  // -------- recall --------
  autoRecall: boolean;
  recallLimit: number;
  scoreThreshold: number;
  minQueryLength: number;
  logRankingDetails: boolean;
  recallMaxContentChars: number;
  recallTokenBudget: number;
  recallPreferAbstract: boolean;

  // -------- capture --------
  autoCapture: boolean;
  captureMode: CaptureMode;
  captureMaxLength: number;
  captureTimeoutMs: number;
  captureAssistantTurns: boolean;
  commitTokenThreshold: number;
  resumeContextBudget: number;

  // -------- bypass / async --------
  bypassSession: boolean;
  bypassSessionPatterns: string[];
  writePathAsync: boolean;

  // -------- debug --------
  debug: boolean;
  debugLogPath: string;
}

export interface LoadConfigOptions {
  /** Default agent id when no env var or config file overrides it. */
  agentIdDefault: AgentIdDefault;
  /**
   * Highest-priority overrides — typically VS Code workspace/user settings.
   * Any field set here wins over env vars and config files. `undefined`
   * fields fall through to the next layer.
   */
  hostOverrides?: Partial<PluginConfig>;
}

interface LoadedFile {
  configPath: string;
  file: Record<string, unknown>;
}

function tryLoadJsonFile(envVar: string, defaultPath: string): LoadedFile | null {
  const raw = process.env[envVar] || defaultPath;
  const configPath = resolvePath(raw.replace(/^~/, homedir()));

  let body: string;
  try {
    body = readFileSync(configPath, "utf-8");
  } catch {
    return null;
  }

  try {
    const parsed = JSON.parse(body);
    if (parsed && typeof parsed === "object") {
      return { configPath, file: parsed as Record<string, unknown> };
    }
    return null;
  } catch {
    return null;
  }
}

function pickRecord(parent: Record<string, unknown>, key: string): Record<string, unknown> {
  const v = parent[key];
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

/**
 * Determine whether the plugin is enabled.
 *
 * Priority:
 *   1. OPENVIKING_MEMORY_ENABLED env var
 *   2. copilot.enabled / claude_code.enabled in ov.conf
 *   3. Whether ov.conf or ovcli.conf exists and is parseable
 */
export function isPluginEnabled(): boolean {
  const envEnabled = envBool("OPENVIKING_MEMORY_ENABLED");
  if (envEnabled !== undefined) return envEnabled;

  const ovConf = tryLoadJsonFile("OPENVIKING_CONFIG_FILE", DEFAULT_OV_CONF_PATH);
  if (ovConf) {
    const cp = pickRecord(ovConf.file, "copilot");
    const cc = pickRecord(ovConf.file, "claude_code");
    if (cp.enabled === false) return false;
    if (Object.keys(cp).length === 0 && cc.enabled === false) return false;
    return true;
  }

  const cliConf = tryLoadJsonFile("OPENVIKING_CLI_CONFIG_FILE", DEFAULT_OVCLI_CONF_PATH);
  if (cliConf) return true;

  return false;
}

function pickHost<K extends keyof PluginConfig>(
  hostOverrides: Partial<PluginConfig> | undefined,
  key: K,
): PluginConfig[K] | undefined {
  if (!hostOverrides) return undefined;
  const v = hostOverrides[key];
  return v as PluginConfig[K] | undefined;
}

/**
 * Load the full plugin configuration.
 *
 * Resolution: hostOverrides -> env -> ovcli.conf -> ov.conf -> defaults.
 */
export function loadConfig(opts: LoadConfigOptions): PluginConfig {
  const { agentIdDefault, hostOverrides } = opts;

  const ovConf = tryLoadJsonFile("OPENVIKING_CONFIG_FILE", DEFAULT_OV_CONF_PATH);
  const cliConf = tryLoadJsonFile("OPENVIKING_CLI_CONFIG_FILE", DEFAULT_OVCLI_CONF_PATH);

  const ovFile = ovConf?.file ?? {};
  const cliFile = cliConf?.file ?? {};
  const configPath = ovConf?.configPath ?? cliConf?.configPath ?? null;

  const server = pickRecord(ovFile, "server");
  const cpBlock = pickRecord(ovFile, "copilot");
  const ccBlock = pickRecord(ovFile, "claude_code");

  // Tuning fields prefer the new `copilot` block; fall through to legacy
  // `claude_code` to keep one config file driving both plugins.
  const tuneStr = (key: string): unknown => cpBlock[key] ?? ccBlock[key];

  // baseUrl: host -> env -> ovcli.url -> ov.server.url -> http://{host}:{port}
  const hostBaseUrl = pickHost(hostOverrides, "baseUrl");
  const envUrl = str(process.env["OPENVIKING_URL"], null) ?? str(process.env["OPENVIKING_BASE_URL"], null);
  let baseUrl: string;
  if (typeof hostBaseUrl === "string" && hostBaseUrl.trim()) {
    baseUrl = hostBaseUrl.trim().replace(/\/+$/, "");
  } else if (envUrl) {
    baseUrl = envUrl.replace(/\/+$/, "");
  } else if (typeof cliFile["url"] === "string" && (cliFile["url"] as string).trim()) {
    baseUrl = (cliFile["url"] as string).trim().replace(/\/+$/, "");
  } else if (typeof server["url"] === "string" && (server["url"] as string).trim()) {
    baseUrl = (server["url"] as string).trim().replace(/\/+$/, "");
  } else {
    const host = str(server["host"], "127.0.0.1").replace("0.0.0.0", "127.0.0.1");
    const port = Math.floor(num(server["port"], 1933));
    baseUrl = `http://${host}:${port}`;
  }

  // apiKey: host -> env (BEARER_TOKEN | API_KEY) -> ovcli.api_key -> tuneStr("apiKey") -> server.root_api_key
  const apiKey = pickHost(hostOverrides, "apiKey")
    ?? str(process.env["OPENVIKING_BEARER_TOKEN"], null)
    ?? str(process.env["OPENVIKING_API_KEY"], null)
    ?? str(cliFile["api_key"], null)
    ?? str(tuneStr("apiKey"), null)
    ?? str(server["root_api_key"], "");

  const agentId = pickHost(hostOverrides, "agentId")
    ?? str(process.env["OPENVIKING_AGENT_ID"], null)
    ?? str(cliFile["agent_id"], null)
    ?? str(tuneStr("agentId"), agentIdDefault);

  const accountId = pickHost(hostOverrides, "accountId")
    ?? str(process.env["OPENVIKING_ACCOUNT"], null)
    ?? str(cliFile["account"], null)
    ?? str(tuneStr("accountId"), "");

  const userId = pickHost(hostOverrides, "userId")
    ?? str(process.env["OPENVIKING_USER"], null)
    ?? str(cliFile["user"], null)
    ?? str(tuneStr("userId"), "");

  const debug = pickHost(hostOverrides, "debug")
    ?? envBool("OPENVIKING_DEBUG")
    ?? (cpBlock["debug"] === true || ccBlock["debug"] === true);

  const debugLogPath = pickHost(hostOverrides, "debugLogPath")
    ?? str(process.env["OPENVIKING_DEBUG_LOG"], null)
    ?? str(tuneStr("debugLogPath"), join(DEFAULT_LOG_DIR, `${agentIdDefault}-hooks.log`));

  const timeoutMs = Math.max(1000, Math.floor(num(
    pickHost(hostOverrides, "timeoutMs")
      ?? num(process.env["OPENVIKING_TIMEOUT_MS"], num(tuneStr("timeoutMs"), 15000)),
    15000,
  )));

  const captureTimeoutMs = Math.max(1000, Math.floor(num(
    pickHost(hostOverrides, "captureTimeoutMs")
      ?? num(process.env["OPENVIKING_CAPTURE_TIMEOUT_MS"], num(tuneStr("captureTimeoutMs"), Math.max(timeoutMs * 2, 30000))),
    Math.max(timeoutMs * 2, 30000),
  )));

  const captureModeRaw = pickHost(hostOverrides, "captureMode")
    ?? str(process.env["OPENVIKING_CAPTURE_MODE"], null)
    ?? str(tuneStr("captureMode"), "semantic");
  const captureMode: CaptureMode = captureModeRaw === "keyword" ? "keyword" : "semantic";

  const envPatterns = str(process.env["OPENVIKING_BYPASS_SESSION_PATTERNS"], null);
  const hostPatterns = pickHost(hostOverrides, "bypassSessionPatterns");
  let bypassSessionPatterns: string[];
  if (Array.isArray(hostPatterns)) {
    bypassSessionPatterns = hostPatterns.filter((p): p is string => typeof p === "string" && !!p.trim());
  } else if (envPatterns) {
    bypassSessionPatterns = envPatterns.split(",").map((s) => s.trim()).filter(Boolean);
  } else {
    const cpPatterns = cpBlock["bypassSessionPatterns"];
    const ccPatterns = ccBlock["bypassSessionPatterns"];
    const fileArr = Array.isArray(cpPatterns) ? cpPatterns : Array.isArray(ccPatterns) ? ccPatterns : [];
    bypassSessionPatterns = fileArr.filter((p): p is string => typeof p === "string" && !!p.trim());
  }

  return {
    configPath,
    baseUrl,
    apiKey,
    agentId,
    accountId,
    userId,
    timeoutMs,

    // -------- recall --------
    autoRecall: pickHost(hostOverrides, "autoRecall")
      ?? envBool("OPENVIKING_AUTO_RECALL")
      ?? (cpBlock["autoRecall"] !== false && ccBlock["autoRecall"] !== false),
    recallLimit: Math.max(1, Math.floor(num(
      pickHost(hostOverrides, "recallLimit")
        ?? num(process.env["OPENVIKING_RECALL_LIMIT"], num(tuneStr("recallLimit"), 6)),
      6,
    ))),
    scoreThreshold: Math.min(1, Math.max(0, num(
      pickHost(hostOverrides, "scoreThreshold")
        ?? num(process.env["OPENVIKING_SCORE_THRESHOLD"], num(tuneStr("scoreThreshold"), 0.35)),
      0.35,
    ))),
    minQueryLength: Math.max(1, Math.floor(num(
      pickHost(hostOverrides, "minQueryLength")
        ?? num(process.env["OPENVIKING_MIN_QUERY_LENGTH"], num(tuneStr("minQueryLength"), 3)),
      3,
    ))),
    logRankingDetails: pickHost(hostOverrides, "logRankingDetails")
      ?? envBool("OPENVIKING_LOG_RANKING_DETAILS")
      ?? (cpBlock["logRankingDetails"] === true || ccBlock["logRankingDetails"] === true),
    recallMaxContentChars: Math.max(50, Math.floor(num(
      pickHost(hostOverrides, "recallMaxContentChars")
        ?? num(process.env["OPENVIKING_RECALL_MAX_CONTENT_CHARS"], num(tuneStr("recallMaxContentChars"), 500)),
      500,
    ))),
    recallTokenBudget: Math.max(200, Math.floor(num(
      pickHost(hostOverrides, "recallTokenBudget")
        ?? num(process.env["OPENVIKING_RECALL_TOKEN_BUDGET"], num(tuneStr("recallTokenBudget"), 2000)),
      2000,
    ))),
    recallPreferAbstract: pickHost(hostOverrides, "recallPreferAbstract")
      ?? envBool("OPENVIKING_RECALL_PREFER_ABSTRACT")
      ?? (cpBlock["recallPreferAbstract"] !== false && ccBlock["recallPreferAbstract"] !== false),

    // -------- capture --------
    autoCapture: pickHost(hostOverrides, "autoCapture")
      ?? envBool("OPENVIKING_AUTO_CAPTURE")
      ?? (cpBlock["autoCapture"] !== false && ccBlock["autoCapture"] !== false),
    captureMode,
    captureMaxLength: Math.max(200, Math.floor(num(
      pickHost(hostOverrides, "captureMaxLength")
        ?? num(process.env["OPENVIKING_CAPTURE_MAX_LENGTH"], num(tuneStr("captureMaxLength"), 24000)),
      24000,
    ))),
    captureTimeoutMs,
    captureAssistantTurns: pickHost(hostOverrides, "captureAssistantTurns")
      ?? envBool("OPENVIKING_CAPTURE_ASSISTANT_TURNS")
      ?? (cpBlock["captureAssistantTurns"] !== false && ccBlock["captureAssistantTurns"] !== false),
    commitTokenThreshold: Math.max(1000, Math.floor(num(
      pickHost(hostOverrides, "commitTokenThreshold")
        ?? num(process.env["OPENVIKING_COMMIT_TOKEN_THRESHOLD"], num(tuneStr("commitTokenThreshold"), 20000)),
      20000,
    ))),
    resumeContextBudget: Math.max(1024, Math.floor(num(
      pickHost(hostOverrides, "resumeContextBudget")
        ?? num(process.env["OPENVIKING_RESUME_CONTEXT_BUDGET"], num(tuneStr("resumeContextBudget"), 32000)),
      32000,
    ))),

    // -------- bypass / async --------
    bypassSession: pickHost(hostOverrides, "bypassSession")
      ?? envBool("OPENVIKING_BYPASS_SESSION")
      ?? false,
    bypassSessionPatterns,
    writePathAsync: pickHost(hostOverrides, "writePathAsync")
      ?? envBool("OPENVIKING_WRITE_PATH_ASYNC")
      ?? (cpBlock["writePathAsync"] !== false && ccBlock["writePathAsync"] !== false),

    debug,
    debugLogPath,
  };
}
