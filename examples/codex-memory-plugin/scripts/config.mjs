/**
 * Shared configuration loader for the Codex OpenViking memory plugin.
 *
 * Credential source:
 *   - Default (auto): env-var credentials win when any credential env var is
 *     set; otherwise the active ovcli.conf is used, so `ov config switch`
 *     changes hooks, MCP, and in-process `ov` commands together on next launch.
 *   - Set OPENVIKING_CREDENTIAL_SOURCE=cli to force ovcli.conf, or =env to
 *     force env-var credentials.
 *   - Without env vars or ovcli.conf, ov.conf/defaults are used.
 *
 * Tuning resolution remains env vars > ov.conf codex.* > built-in defaults.
 *
 * The stdio MCP proxy calls the same resolver directly. Aligning the resolver
 * prevents identity drift between auto-capture/auto-recall hooks, MCP calls,
 * and child `ov` commands launched from inside Codex.
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
 *   OPENVIKING_AUTH_MODE
 *   OPENVIKING_ACCOUNT, OPENVIKING_USER, OPENVIKING_PEER_ID
 *
 * Misc env vars:
 *   OPENVIKING_TIMEOUT_MS, OPENVIKING_CAPTURE_TIMEOUT_MS
 *   OPENVIKING_RECALL_TIMEOUT_MS, OPENVIKING_RECALL_COMPRESS_TIMEOUT_MS
 *   OPENVIKING_RECALL_COMPRESS_MODEL, OPENVIKING_RECALL_COMPRESS_THINKING
 *   OPENVIKING_RECALL_COMPRESS_BASE_URL
 *   OPENVIKING_RECALL_LIMIT, OPENVIKING_SCORE_THRESHOLD
 *   OPENVIKING_WORKSPACE_PEER, OPENVIKING_RECALL_PEER_SCOPE
 *   OPENVIKING_NO_AUTO_INJECT, OPENVIKING_PROFILE_TOKEN_BUDGET
 *   OPENVIKING_DEBUG=1, OPENVIKING_DEBUG_LOG
 */

import { homedir } from "node:os";
import { join } from "node:path";
import { resolveOpenVikingCredentials } from "./ov-credentials.mjs";
import { buildUserAgent, readManifestVersion } from "./shared/credentials.mjs";
import { HARNESS_KEYS, loadPluginSettings } from "./shared/plugin-config.mjs";

const USER_AGENT = buildUserAgent(
  "codex",
  readManifestVersion(new URL("../.codex-plugin/plugin.json", import.meta.url)),
);

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
  if (lower === "0" || lower === "false" || lower === "no" || lower === "off") return false;
  if (lower === "1" || lower === "true" || lower === "yes" || lower === "on"
      || lower === "auto" || lower === "client") return true;
  return undefined;
}

function configBool(value, fallback) {
  if (typeof value === "boolean") return value;
  const lower = String(value ?? "").trim().toLowerCase();
  if (lower === "0" || lower === "false" || lower === "no" || lower === "off") return false;
  if (lower === "1" || lower === "true" || lower === "yes" || lower === "on"
      || lower === "auto" || lower === "client") return true;
  return fallback;
}

function hasOwn(obj, key) {
  return Object.prototype.hasOwnProperty.call(obj || {}, key);
}

function normalizeAuthMode(val) {
  const mode = str(val, "").toLowerCase();
  return ["trusted", "api_key"].includes(mode) ? mode : "";
}

export function loadConfig() {
  const creds = resolveOpenVikingCredentials();
  const { cliPath, ovFile, ovPath } = creds;
  const configPath = cliPath || ovPath || null;

  // ovcli.conf plugin.<harness> overrides plugin.* which overrides ov.conf's
  // codex section, so client-side tuning no longer needs a server config.
  const cx = { ...(ovFile.codex || {}), ...loadPluginSettings(HARNESS_KEYS.codex) };
  const server = ovFile.server || {};
  const explicitAuthMode = normalizeAuthMode(process.env.OPENVIKING_AUTH_MODE)
    || normalizeAuthMode(cx.authMode)
    || normalizeAuthMode(cx.auth_mode)
    || normalizeAuthMode(server.auth_mode);
  const authMode = explicitAuthMode || ((creds.account || creds.user) ? "trusted" : "api_key");

  const debug = envBool("OPENVIKING_DEBUG") ?? (cx.debug === true);
  const defaultLogPath = join(homedir(), ".openviking", "logs", "codex-hooks.log");
  const debugLogPath = str(process.env.OPENVIKING_DEBUG_LOG, defaultLogPath);
  const workspacePeer = envBool("OPENVIKING_WORKSPACE_PEER") ?? (cx.workspacePeer !== false);
  const recallPeerScopeRaw = str(
    process.env.OPENVIKING_RECALL_PEER_SCOPE,
    str(cx.recallPeerScope, "all"),
  );
  const recallPeerScope = recallPeerScopeRaw === "actor" ? "actor" : "all";

  const timeoutMs = Math.max(1000, Math.floor(num(
    process.env.OPENVIKING_TIMEOUT_MS,
    num(cx.timeoutMs, 15000),
  )));
  const captureTimeoutMs = Math.max(1000, Math.floor(num(
    process.env.OPENVIKING_CAPTURE_TIMEOUT_MS,
    num(cx.captureTimeoutMs, Math.max(timeoutMs * 2, 30000)),
  )));
  const recallTimeoutMs = Math.max(1000, Math.floor(num(
    process.env.OPENVIKING_RECALL_TIMEOUT_MS,
    num(cx.recallTimeoutMs, 120000),
  )));
  const defaultRecallCompressTimeoutMs = Math.max(1000, recallTimeoutMs - 10000);
  const recallCompressTimeoutMs = Math.max(1000, Math.floor(num(
    process.env.OPENVIKING_RECALL_COMPRESS_TIMEOUT_MS,
    num(cx.recallCompressTimeoutMs, defaultRecallCompressTimeoutMs),
  )));
  const recallCompressModel = str(
    process.env.OPENVIKING_RECALL_COMPRESS_MODEL,
    hasOwn(cx, "recallCompressModel") ? str(cx.recallCompressModel, "") : "",
  );
  const recallCompressBaseUrl = str(
    process.env.OPENVIKING_RECALL_COMPRESS_BASE_URL,
    hasOwn(cx, "recallCompressBaseUrl") ? str(cx.recallCompressBaseUrl, "") : "",
  );
  const cxRecallCompressThinking = hasOwn(cx, "recallCompressThinking")
    ? cx.recallCompressThinking
    : (hasOwn(cx, "recallCompressReasoningEffort") ? cx.recallCompressReasoningEffort : "");
  const recallCompressThinking = str(
    process.env.OPENVIKING_RECALL_COMPRESS_THINKING,
    str(
      process.env.OPENVIKING_RECALL_COMPRESS_REASONING_EFFORT,
      str(cxRecallCompressThinking, ""),
    ),
  );

  return {
    configPath,
    cliConfigPath: cliPath,
    ovConfigPath: ovPath,
    credentialSource: creds.credentialSource,
    baseUrl: creds.baseUrl,
    authMode,
    sendIdentityHeaders: authMode === "trusted",
    apiKey: creds.apiKey,
    account: creds.account,
    user: creds.user,
    peerId: creds.peerId,
    workspacePeer,
    userAgent: USER_AGENT,
    timeoutMs,
    recallTimeoutMs,

    autoRecall: envBool("OPENVIKING_AUTO_RECALL") ?? (cx.autoRecall !== false),
    recallLimit: Math.max(1, Math.floor(num(
      process.env.OPENVIKING_RECALL_LIMIT,
      num(cx.recallLimit, 10),
    ))),
    recallLimitConfigured: Boolean(process.env.OPENVIKING_RECALL_LIMIT) ||
      hasOwn(cx, "recallLimit"),
    scoreThreshold: Math.min(1, Math.max(0, num(
      process.env.OPENVIKING_SCORE_THRESHOLD,
      num(cx.scoreThreshold, 0.35),
    ))),
    minQueryLength: Math.max(1, Math.floor(num(
      process.env.OPENVIKING_MIN_QUERY_LENGTH,
      num(cx.minQueryLength, 3),
    ))),
    logRankingDetails: envBool("OPENVIKING_LOG_RANKING_DETAILS") ?? (cx.logRankingDetails === true),
    recallPeerScope,
    recallCompress: envBool("OPENVIKING_RECALL_COMPRESS") ?? configBool(cx.recallCompress, true),
    recallCompressModel,
    recallCompressBaseUrl,
    recallCompressThinking,
    recallCompressConfigured: Boolean(recallCompressModel || recallCompressThinking),
    recallCompressTimeoutMs,
    recallCompressDetectOnStartup: envBool("OPENVIKING_RECALL_COMPRESS_DETECT_ON_STARTUP") ?? (cx.recallCompressDetectOnStartup !== false),
    recallCompressDetectTimeoutMs: Math.max(1000, Math.floor(num(
      process.env.OPENVIKING_RECALL_COMPRESS_DETECT_TIMEOUT_MS,
      num(cx.recallCompressDetectTimeoutMs, 15000),
    ))),
    recallCompressDetectTtlMs: Math.max(0, Math.floor(num(
      process.env.OPENVIKING_RECALL_COMPRESS_DETECT_TTL_MS,
      num(cx.recallCompressDetectTtlMs, 604800000),
    ))),
    recallCompressMaxInputChars: Math.max(1000, Math.floor(num(
      process.env.OPENVIKING_RECALL_COMPRESS_MAX_INPUT_CHARS,
      num(cx.recallCompressMaxInputChars, 18000),
    ))),
    recallCompressMaxBullets: Math.max(1, Math.floor(num(
      process.env.OPENVIKING_RECALL_COMPRESS_MAX_BULLETS,
      num(cx.recallCompressMaxBullets, 6),
    ))),
    recallCompressMaxBulletsConfigured:
      Boolean(process.env.OPENVIKING_RECALL_COMPRESS_MAX_BULLETS) ||
      hasOwn(cx, "recallCompressMaxBullets"),

    // Server-side context assembly (/search mode="context").
    recallMaxTokens: Math.max(64, Math.floor(num(
      process.env.OPENVIKING_RECALL_MAX_TOKENS,
      num(cx.recallMaxTokens, 1600),
    ))),
    recallMaxTokensConfigured: Boolean(process.env.OPENVIKING_RECALL_MAX_TOKENS) ||
      hasOwn(cx, "recallMaxTokens"),
    recallDedupTurns: Math.max(0, Math.floor(num(
      process.env.OPENVIKING_RECALL_DEDUP_TURNS,
      num(cx.recallDedupTurns, 5),
    ))),
    recallQueryExpansion: str(
      process.env.OPENVIKING_RECALL_QUERY_EXPANSION,
      str(cx.recallQueryExpansion, "auto"),
    ) === "off" ? "off" : "auto",
    recallQueryExpansionConfigured: Boolean(process.env.OPENVIKING_RECALL_QUERY_EXPANSION) ||
      hasOwn(cx, "recallQueryExpansion"),

    autoCapture: envBool("OPENVIKING_AUTO_CAPTURE") ?? (cx.autoCapture !== false),
    captureMode: (str(process.env.OPENVIKING_CAPTURE_MODE, str(cx.captureMode, "semantic")) === "keyword")
      ? "keyword"
      : "semantic",
    captureMaxLength: Math.max(200, Math.floor(num(
      process.env.OPENVIKING_CAPTURE_MAX_LENGTH,
      num(cx.captureMaxLength, 24000),
    ))),
    captureTimeoutMs,
    captureToolMaxChars: Math.max(200, Math.floor(num(
      process.env.OPENVIKING_CAPTURE_TOOL_MAX_CHARS,
      num(cx.captureToolMaxChars, 1000000),
    ))),
    writePathAsync: envBool("OPENVIKING_WRITE_PATH_ASYNC") ?? (cx.writePathAsync !== false),
    // Default true: a "memory plugin" without assistant-side capture only sees half the
    // conversation, which makes extraction noticeably worse. Mirrors the claude-code plugin
    // (examples/claude-code-memory-plugin/scripts/config.mjs). Operators who want the old
    // user-only behavior can set OPENVIKING_CAPTURE_ASSISTANT_TURNS=0 or codex.captureAssistantTurns=false.
    captureAssistantTurns: envBool("OPENVIKING_CAPTURE_ASSISTANT_TURNS") ?? (cx.captureAssistantTurns !== false),
    captureLastAssistantOnStop: envBool("OPENVIKING_CAPTURE_LAST_ASSISTANT_ON_STOP") ?? (cx.captureLastAssistantOnStop !== false),
    commitTokenThreshold: Math.max(1000, Math.floor(num(
      process.env.OPENVIKING_COMMIT_TOKEN_THRESHOLD,
      num(cx.commitTokenThreshold, 20000),
    ))),
    commitKeepRecentCount: Math.max(0, Math.floor(num(
      process.env.OPENVIKING_COMMIT_KEEP_RECENT_COUNT,
      num(cx.commitKeepRecentCount, 10),
    ))),

    autoCommitOnCompact: envBool("OPENVIKING_AUTO_COMMIT_ON_COMPACT") ?? (cx.autoCommitOnCompact !== false),
    noAutoInject: envBool("OPENVIKING_NO_AUTO_INJECT") ?? (cx.noAutoInject === true),
    profileTokenBudget: Math.max(500, Math.floor(num(
      process.env.OPENVIKING_PROFILE_TOKEN_BUDGET,
      num(cx.profileTokenBudget, 10000),
    ))),
    resumeArchiveInject: envBool("OPENVIKING_RESUME_ARCHIVE_INJECT") ?? (cx.resumeArchiveInject !== false),
    resumeArchiveTokenBudget: Math.max(0, Math.floor(num(
      process.env.OPENVIKING_RESUME_ARCHIVE_TOKEN_BUDGET,
      num(cx.resumeArchiveTokenBudget, 32000),
    ))),
    resumeArchiveMaxChars: Math.max(1000, Math.floor(num(
      process.env.OPENVIKING_RESUME_ARCHIVE_MAX_CHARS,
      num(cx.resumeArchiveMaxChars, 6000),
    ))),

    debug,
    debugLogPath,
  };
}
