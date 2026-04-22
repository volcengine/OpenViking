import { homedir } from "node:os";
import { join } from "node:path";
import { resolve as resolvePath } from "node:path";
import { getEnv } from "./runtime-utils.js";

export type MemoryOpenVikingConfig = {
  /** "local" = plugin starts OpenViking server as child process (like Claude Code); "remote" = use existing HTTP server */
  mode?: "local" | "remote";
  /** Path to ov.conf; used when mode is "local". Default ~/.openviking/ov.conf */
  configPath?: string;
  /** Port for local server when mode is "local". Ignored when mode is "remote". */
  port?: number;
  baseUrl?: string;
  agentId?: string;
  serverAuthMode?: "api_key" | "trusted";
  apiKey?: string;
  /** Advanced option. Only needed when using root key or trusted auth mode. With a user key the server derives identity from the key. */
  accountId?: string;
  /** Advanced option. Only needed when using root key or trusted auth mode. */
  userId?: string;
  /**
   * Canonical namespace policy. Must match the server-side account namespace
   * policy because current /system/status does not expose it.
   */
  isolateUserScopeByAgent?: boolean;
  isolateAgentScopeByUser?: boolean;
  /**
   * Deprecated compatibility alias for older hash-based agent space behavior.
   * Prefer isolateUserScopeByAgent / isolateAgentScopeByUser.
   */
  agentScopeMode?: "user_agent" | "agent";
  targetUri?: string;
  timeoutMs?: number;
  autoCapture?: boolean;
  captureMode?: "semantic" | "keyword";
  captureMaxLength?: number;
  autoRecall?: boolean;
  /** Include resources in auto-recall and default memory_recall search. Default false. */
  recallResources?: boolean;
  recallLimit?: number;
  recallScoreThreshold?: number;
  recallMaxContentChars?: number;
  recallPreferAbstract?: boolean;
  recallTokenBudget?: number;
  commitTokenThreshold?: number;
  bypassSessionPatterns?: string[];
  /**
   * When true (default), emit structured `openviking: diag {...}` lines (and any future
   * standard-diagnostics file writes) for assemble/afterTurn. Set false to disable.
   */
  emitStandardDiagnostics?: boolean;
  /** When true, log tenant routing for semantic find and session writes (messages/commit) to the plugin logger. */
  logFindRequests?: boolean;
};

const DEFAULT_BASE_URL = "http://127.0.0.1:1933";
const DEFAULT_PORT = 1933;
const DEFAULT_TARGET_URI = "viking://user/memories";
const DEFAULT_TIMEOUT_MS = 15000;
const DEFAULT_CAPTURE_MODE = "semantic";
const DEFAULT_CAPTURE_MAX_LENGTH = 24000;
const DEFAULT_RECALL_LIMIT = 6;
const DEFAULT_RECALL_SCORE_THRESHOLD = 0.15;
const DEFAULT_RECALL_MAX_CONTENT_CHARS = 5000;
const DEFAULT_RECALL_PREFER_ABSTRACT = true;
const DEFAULT_RECALL_TOKEN_BUDGET = 8000;
const DEFAULT_COMMIT_TOKEN_THRESHOLD = 20000;
const DEFAULT_BYPASS_SESSION_PATTERNS: string[] = [];
const DEFAULT_EMIT_STANDARD_DIAGNOSTICS = false;
const DEFAULT_LOCAL_CONFIG_PATH = join(homedir(), ".openviking", "ov.conf");

const DEFAULT_AGENT_ID = "default";
const DEFAULT_SERVER_AUTH_MODE = "api_key";

function resolveAgentId(configured: unknown): string {
  if (typeof configured === "string" && configured.trim()) {
    return configured.trim();
  }
  return DEFAULT_AGENT_ID;
}

function resolveEnvVars(value: string): string {
  return value.replace(/\$\{([^}]+)\}/g, (_, envVar) => {
    const envValue = getEnv(envVar);
    if (!envValue) {
      throw new Error(`Environment variable ${envVar} is not set`);
    }
    return envValue;
  });
}

function toNumber(value: unknown, fallback: number): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return fallback;
}

function toStringArray(value: unknown, fallback: string[]): string[] {
  if (Array.isArray(value)) {
    return value
      .filter((entry): entry is string => typeof entry === "string")
      .map((entry) => entry.trim())
      .filter(Boolean);
  }
  if (typeof value === "string") {
    return value
      .split(/[,\n]/)
      .map((entry) => entry.trim())
      .filter(Boolean);
  }
  return fallback;
}

/** True when env is 1 / true / yes (case-insensitive). Used for debug flags without editing plugin JSON. */
function envFlag(name: string): boolean {
  const v = getEnv(name);
  if (v == null || v === "") {
    return false;
  }
  const t = String(v).trim().toLowerCase();
  return t === "1" || t === "true" || t === "yes";
}

function assertAllowedKeys(value: Record<string, unknown>, allowed: string[], label: string) {
  const unknown = Object.keys(value).filter((key) => !allowed.includes(key));
  if (unknown.length === 0) {
    return;
  }
  throw new Error(`${label} has unknown keys: ${unknown.join(", ")}`);
}

function resolveDefaultBaseUrl(): string {
  const fromEnv = getEnv("OPENVIKING_BASE_URL") || getEnv("OPENVIKING_URL");
  if (fromEnv) {
    return fromEnv;
  }
  return DEFAULT_BASE_URL;
}

export const memoryOpenVikingConfigSchema = {
  parse(value: unknown): Required<MemoryOpenVikingConfig> {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      value = {};
    }
    const cfg = value as Record<string, unknown>;
    assertAllowedKeys(
      cfg,
      [
        "mode",
        "configPath",
        "port",
        "baseUrl",
        "agentId",
        "serverAuthMode",
        "apiKey",
        "accountId",
        "userId",
        "isolateUserScopeByAgent",
        "isolateAgentScopeByUser",
        "agentScopeMode",
        "targetUri",
        "timeoutMs",
        "autoCapture",
        "captureMode",
        "captureMaxLength",
        "autoRecall",
        "recallResources",
        "recallLimit",
        "recallScoreThreshold",
        "recallMaxContentChars",
        "recallPreferAbstract",
        "recallTokenBudget",
        "commitTokenThreshold",
        "bypassSessionPatterns",
        "ingestReplyAssist",
        "ingestReplyAssistMinSpeakerTurns",
        "ingestReplyAssistMinChars",
        "ingestReplyAssistIgnoreSessionPatterns",
        "emitStandardDiagnostics",
        "logFindRequests",
      ],
      "openviking config",
    );

    const mode = (cfg.mode === "local" || cfg.mode === "remote" ? cfg.mode : "remote") as
      | "local"
      | "remote";
    const port = Math.max(1, Math.min(65535, Math.floor(toNumber(cfg.port, DEFAULT_PORT))));
    const rawConfigPath =
      typeof cfg.configPath === "string" && cfg.configPath.trim()
        ? cfg.configPath.trim()
        : DEFAULT_LOCAL_CONFIG_PATH;
    const configPath = resolvePath(
      resolveEnvVars(rawConfigPath).replace(/^~/, homedir()),
    );

    const localBaseUrl = `http://127.0.0.1:${port}`;
    const rawBaseUrl =
      mode === "local" ? localBaseUrl : (typeof cfg.baseUrl === "string" ? cfg.baseUrl : resolveDefaultBaseUrl());
    const resolvedBaseUrl = resolveEnvVars(rawBaseUrl).replace(/\/+$/, "");
    const rawApiKey = typeof cfg.apiKey === "string" ? cfg.apiKey : process.env.OPENVIKING_API_KEY;
    const rawServerAuthMode =
      cfg.serverAuthMode ?? process.env.OPENVIKING_SERVER_AUTH_MODE;
    const serverAuthMode =
      rawServerAuthMode === "trusted" ? "trusted" as const : "api_key" as const;
    const captureMode = cfg.captureMode;
    if (
      typeof captureMode !== "undefined" &&
      captureMode !== "semantic" &&
      captureMode !== "keyword"
    ) {
      throw new Error(`openviking captureMode must be "semantic" or "keyword"`);
    }

    const accountId =
      typeof cfg.accountId === "string" && cfg.accountId.trim()
        ? cfg.accountId.trim()
        : (process.env.OPENVIKING_ACCOUNT_ID?.trim() || "");
    const userId =
      typeof cfg.userId === "string" && cfg.userId.trim()
        ? cfg.userId.trim()
        : (process.env.OPENVIKING_USER_ID?.trim() || "");

    const hasExplicitAgentScopeMode =
      typeof cfg.agentScopeMode === "string" || process.env.OPENVIKING_AGENT_SCOPE_MODE !== undefined;
    const rawAgentScope = cfg.agentScopeMode ?? process.env.OPENVIKING_AGENT_SCOPE_MODE;
    const agentScopeMode =
      rawAgentScope === "user_agent" ? "user_agent" as const : "agent" as const;
    const explicitIsolateUserScopeByAgent =
      typeof cfg.isolateUserScopeByAgent === "boolean"
        ? cfg.isolateUserScopeByAgent
        : undefined;
    const explicitIsolateAgentScopeByUser =
      typeof cfg.isolateAgentScopeByUser === "boolean"
        ? cfg.isolateAgentScopeByUser
        : undefined;
    const envIsolateUserScopeByAgent =
      explicitIsolateUserScopeByAgent === undefined &&
      process.env.OPENVIKING_ISOLATE_USER_SCOPE_BY_AGENT !== undefined
        ? envFlag("OPENVIKING_ISOLATE_USER_SCOPE_BY_AGENT")
        : undefined;
    const envIsolateAgentScopeByUser =
      explicitIsolateAgentScopeByUser === undefined &&
      process.env.OPENVIKING_ISOLATE_AGENT_SCOPE_BY_USER !== undefined
        ? envFlag("OPENVIKING_ISOLATE_AGENT_SCOPE_BY_USER")
        : undefined;
    const isolateUserScopeByAgent =
      explicitIsolateUserScopeByAgent ??
      envIsolateUserScopeByAgent ??
      false;
    const isolateAgentScopeByUser =
      explicitIsolateAgentScopeByUser ??
      envIsolateAgentScopeByUser ??
      (hasExplicitAgentScopeMode && agentScopeMode === "user_agent" ? true : false);

    return {
      mode,
      configPath,
      port,
      baseUrl: resolvedBaseUrl,
      agentId: resolveAgentId(cfg.agentId),
      serverAuthMode,
      apiKey: rawApiKey ? resolveEnvVars(rawApiKey) : "",
      accountId,
      userId,
      isolateUserScopeByAgent,
      isolateAgentScopeByUser,
      agentScopeMode,
      targetUri: typeof cfg.targetUri === "string" ? cfg.targetUri : DEFAULT_TARGET_URI,
      timeoutMs: Math.max(1000, Math.floor(toNumber(cfg.timeoutMs, DEFAULT_TIMEOUT_MS))),
      autoCapture: cfg.autoCapture !== false,
      captureMode: captureMode ?? DEFAULT_CAPTURE_MODE,
      captureMaxLength: Math.max(
        200,
        Math.min(200_000, Math.floor(toNumber(cfg.captureMaxLength, DEFAULT_CAPTURE_MAX_LENGTH))),
      ),
      autoRecall: cfg.autoRecall !== false,
      recallResources: cfg.recallResources === true || envFlag("OPENVIKING_RECALL_RESOURCES"),
      recallLimit: Math.max(1, Math.floor(toNumber(cfg.recallLimit, DEFAULT_RECALL_LIMIT))),
      recallScoreThreshold: Math.min(
        1,
        Math.max(0, toNumber(cfg.recallScoreThreshold, DEFAULT_RECALL_SCORE_THRESHOLD)),
      ),
      recallMaxContentChars: Math.max(
        50,
        Math.min(10000, Math.floor(toNumber(cfg.recallMaxContentChars, DEFAULT_RECALL_MAX_CONTENT_CHARS))),
      ),
      recallPreferAbstract: cfg.recallPreferAbstract === true,
      recallTokenBudget: Math.max(
        100,
        Math.min(50000, Math.floor(toNumber(cfg.recallTokenBudget, DEFAULT_RECALL_TOKEN_BUDGET))),
      ),
      commitTokenThreshold: Math.max(
        0,
        Math.min(100_000, Math.floor(toNumber(cfg.commitTokenThreshold, DEFAULT_COMMIT_TOKEN_THRESHOLD))),
      ),
      bypassSessionPatterns: toStringArray(
        cfg.bypassSessionPatterns,
        toStringArray(
          cfg.ingestReplyAssistIgnoreSessionPatterns,
          DEFAULT_BYPASS_SESSION_PATTERNS,
        ),
      ),
      emitStandardDiagnostics:
        typeof cfg.emitStandardDiagnostics === "boolean"
          ? cfg.emitStandardDiagnostics
          : DEFAULT_EMIT_STANDARD_DIAGNOSTICS,
      logFindRequests:
        cfg.logFindRequests === true ||
        envFlag("OPENVIKING_LOG_ROUTING") ||
        envFlag("OPENVIKING_DEBUG"),
    };
  },
  uiHints: {
    mode: {
      label: "Mode",
      help: "local = plugin starts OpenViking server (like Claude Code); remote = use existing HTTP server",
    },
    configPath: {
      label: "Config path (local)",
      placeholder: DEFAULT_LOCAL_CONFIG_PATH,
      help: "Path to ov.conf when mode is local",
    },
    port: {
      label: "Port (local)",
      placeholder: String(DEFAULT_PORT),
      help: "Port for local OpenViking server",
      advanced: true,
    },
    baseUrl: {
      label: "OpenViking Base URL (remote)",
      placeholder: DEFAULT_BASE_URL,
      help: "HTTP URL when mode is remote (or use ${OPENVIKING_BASE_URL})",
    },
    agentId: {
      label: "Agent ID",
      placeholder: "auto-generated",
      help: 'OpenViking X-OpenViking-Agent. "default" follows OpenClaw ctx.agentId. Non-default values are prepended as "<config>_<ctx.agentId>" (sanitized to [a-zA-Z0-9_-]).',
    },
    serverAuthMode: {
      label: "Server Auth Mode",
      placeholder: DEFAULT_SERVER_AUTH_MODE,
      help: 'OpenViking auth behavior. "api_key" (default): send X-API-Key when configured, otherwise dev fallback to X-OpenViking-Account/User default/default. "trusted": always send accountId/userId and optionally send apiKey when configured.',
    },
    apiKey: {
      label: "OpenViking API Key",
      sensitive: true,
      placeholder: "${OPENVIKING_API_KEY}",
      help: "Optional API key for OpenViking server",
    },
    accountId: {
      label: "Account ID",
      placeholder: "(derived from API key)",
      help: "Advanced option. Tenant account ID. Only needed when using root key or trusted auth mode. With a user key the server derives identity from the key.",
      advanced: true,
    },
    userId: {
      label: "User ID",
      placeholder: "(derived from API key)",
      help: "Advanced option. Tenant user ID. Only needed when using root key or trusted auth mode.",
      advanced: true,
    },
    isolateUserScopeByAgent: {
      label: "Isolate User Scope By Agent",
      placeholder: "false",
      help: "Canonical namespace policy. false (default): user alias expands to viking://user/<user_id>/... . true: expands to viking://user/<user_id>/agent/<agent_id>/... . Must match the server-side account namespace policy.",
      advanced: true,
    },
    isolateAgentScopeByUser: {
      label: "Isolate Agent Scope By User",
      placeholder: "false",
      help: "Canonical namespace policy. false (default): agent alias expands to viking://agent/<agent_id>/... . true: expands to viking://agent/<agent_id>/user/<user_id>/... . Must match the server-side account namespace policy.",
      advanced: true,
    },
    agentScopeMode: {
      label: "Deprecated Agent Scope Mode",
      placeholder: "agent",
      help: 'Deprecated compatibility alias for older routing behavior. Prefer isolateUserScopeByAgent / isolateAgentScopeByUser. Mapping: explicit "user_agent" => false/true, explicit "agent" => false/false. When fully unset, the plugin defaults to false/false to match the current server-side default policy.',
      advanced: true,
    },
    targetUri: {
      label: "Search Target URI",
      placeholder: DEFAULT_TARGET_URI,
      help: "Default OpenViking target URI for memory search",
    },
    timeoutMs: {
      label: "Request Timeout (ms)",
      placeholder: String(DEFAULT_TIMEOUT_MS),
      advanced: true,
    },
    autoCapture: {
      label: "Auto-Capture",
      help: "Extract memories from recent conversation messages via OpenViking sessions",
    },
    captureMode: {
      label: "Capture Mode",
      placeholder: DEFAULT_CAPTURE_MODE,
      advanced: true,
      help: '"semantic" captures all eligible user text and relies on OpenViking extraction; "keyword" uses trigger regex first.',
    },
    captureMaxLength: {
      label: "Capture Max Length",
      placeholder: String(DEFAULT_CAPTURE_MAX_LENGTH),
      advanced: true,
      help: "Maximum sanitized user text length allowed for auto-capture.",
    },
    autoRecall: {
      label: "Auto-Recall",
      help: "Inject relevant OpenViking memories into agent context",
    },
    recallResources: {
      label: "Recall Resources",
      help: "Include resources (viking://resources) in auto-recall and default memory_recall search. Enables account-level shared knowledge retrieval.",
      advanced: true,
    },
    recallLimit: {
      label: "Recall Limit",
      placeholder: String(DEFAULT_RECALL_LIMIT),
      advanced: true,
    },
    recallScoreThreshold: {
      label: "Recall Score Threshold",
      placeholder: String(DEFAULT_RECALL_SCORE_THRESHOLD),
      advanced: true,
    },
    recallMaxContentChars: {
      label: "Recall Max Content Chars",
      placeholder: String(DEFAULT_RECALL_MAX_CONTENT_CHARS),
      advanced: true,
      help: "Maximum characters per memory content in auto-recall injection. Content exceeding this is truncated.",
    },
    recallPreferAbstract: {
      label: "Recall Prefer Abstract",
      advanced: true,
      help: "Use memory abstract instead of fetching full content when abstract is available. Reduces token usage.",
    },
    recallTokenBudget: {
      label: "Recall Token Budget",
      placeholder: String(DEFAULT_RECALL_TOKEN_BUDGET),
      advanced: true,
      help: "Maximum estimated tokens for auto-recall memory injection. Injection stops when budget is exhausted.",
    },
    bypassSessionPatterns: {
      label: "Bypass Session Patterns",
      placeholder: "agent:*:cron:**",
      help: "Completely bypass OpenViking for matching session keys. Use * within one segment and ** across segments.",
      advanced: true,
    },
    commitTokenThreshold: {
      label: "Commit Token Threshold",
      placeholder: String(DEFAULT_COMMIT_TOKEN_THRESHOLD),
      advanced: true,
      help: "Minimum estimated pending tokens before auto-commit triggers. Set to 0 to commit every turn.",
    },
    emitStandardDiagnostics: {
      label: "Standard diagnostics (diag JSON lines)",
      advanced: true,
      help: "When enabled, emit structured openviking: diag {...} lines for assemble and afterTurn. Disable to reduce log noise.",
    },
    logFindRequests: {
      label: "Log find requests",
      help:
        "Log tenant routing: POST /api/v1/search/find (query, target_uri) and session POST .../messages + .../commit (sessionId, X-OpenViking-*). Never logs apiKey. " +
        "Or set env OPENVIKING_LOG_ROUTING=1 or OPENVIKING_DEBUG=1 (no JSON edit). When on, local-mode OpenViking subprocess stderr is also logged at info.",
      advanced: true,
    },
  },
};

export const DEFAULT_MEMORY_OPENVIKING_DATA_DIR = join(
  homedir(),
  ".openclaw",
  "memory",
  "openviking",
);
