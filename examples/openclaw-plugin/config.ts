import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { resolve as resolvePath } from "node:path";

export type MemoryOpenVikingConfig = {
  /** "local" = plugin starts OpenViking server as child process (like Claude Code); "remote" = use existing HTTP server */
  mode?: "local" | "remote";
  /** Path to ov.conf; used when mode is "local". Default ~/.openviking/ov.conf */
  configPath?: string;
  /** Port for local server when mode is "local". Ignored when mode is "remote". */
  port?: number;
  baseUrl?: string;
  account?: string;
  user?: string;
  agentId?: string;
  apiKey?: string;
  targetUri?: string;
  timeoutMs?: number;
  autoCapture?: boolean;
  captureMode?: "semantic" | "keyword";
  captureMaxLength?: number;
  autoRecall?: boolean;
  recallPath?: "assemble" | "hook";
  recallLimit?: number;
  recallScoreThreshold?: number;
  recallMaxContentChars?: number;
  recallPreferAbstract?: boolean;
  recallTokenBudget?: number;
  adaptiveRecall?: boolean;
  recallCacheTtlMs?: number;
  recallFastMaxAgeMs?: number;
  recallBackgroundRefresh?: boolean;
  recallTierOverrides?: {
    full?: string[];
    none?: string[];
  };
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
const DEFAULT_RECALL_PATH = "assemble";
const DEFAULT_RECALL_SCORE_THRESHOLD = 0.15;
const DEFAULT_RECALL_MAX_CONTENT_CHARS = 500;
const DEFAULT_RECALL_PREFER_ABSTRACT = true;
const DEFAULT_RECALL_TOKEN_BUDGET = 2000;
const DEFAULT_ADAPTIVE_RECALL = true;
const DEFAULT_RECALL_CACHE_TTL_MS = 600_000;
const DEFAULT_RECALL_FAST_MAX_AGE_MS = 600_000;
const DEFAULT_RECALL_BACKGROUND_REFRESH = true;
const DEFAULT_RECALL_TIER_OVERRIDES = { full: [] as string[], none: [] as string[] };
const DEFAULT_COMMIT_TOKEN_THRESHOLD = 20000;
const DEFAULT_BYPASS_SESSION_PATTERNS: string[] = [];
const DEFAULT_EMIT_STANDARD_DIAGNOSTICS = false;
const DEFAULT_LOCAL_CONFIG_PATH = join(homedir(), ".openviking", "ov.conf");

const DEFAULT_AGENT_ID = "default";

type OpenVikingConfigIdentity = {
  account?: string;
  user?: string;
  agent?: string;
};

function toNonEmptyString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function readOpenVikingConfigIdentity(configPath: string): OpenVikingConfigIdentity {
  try {
    const raw = JSON.parse(readFileSync(configPath, "utf8")) as Record<string, unknown>;
    return {
      account: toNonEmptyString(raw.default_account),
      user: toNonEmptyString(raw.default_user),
      agent: toNonEmptyString(raw.default_agent),
    };
  } catch {
    return {};
  }
}

function resolveIdentityField(configured: unknown, envValue: unknown, fileValue?: string): string {
  return toNonEmptyString(configured) ?? toNonEmptyString(envValue) ?? fileValue ?? "";
}

function resolveAgentId(configured: unknown): string {
  if (typeof configured === "string" && configured.trim()) {
    return configured.trim();
  }
  return DEFAULT_AGENT_ID;
}

function resolveEnvVars(value: string): string {
  return value.replace(/\$\{([^}]+)\}/g, (_, envVar) => {
    const envValue = process.env[envVar];
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

function toRecallTierOverrides(value: unknown): { full: string[]; none: string[] } {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {
      full: [...DEFAULT_RECALL_TIER_OVERRIDES.full],
      none: [...DEFAULT_RECALL_TIER_OVERRIDES.none],
    };
  }
  const raw = value as Record<string, unknown>;
  assertAllowedKeys(raw, ["full", "none"], "openviking recallTierOverrides");
  return {
    full: toStringArray(raw.full, DEFAULT_RECALL_TIER_OVERRIDES.full),
    none: toStringArray(raw.none, DEFAULT_RECALL_TIER_OVERRIDES.none),
  };
}

/** True when env is 1 / true / yes (case-insensitive). Used for debug flags without editing plugin JSON. */
function envFlag(name: string): boolean {
  const v = process.env[name];
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
  const fromEnv = process.env.OPENVIKING_BASE_URL || process.env.OPENVIKING_URL;
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
        "account",
        "user",
        "agentId",
        "apiKey",
        "targetUri",
        "timeoutMs",
        "autoCapture",
        "captureMode",
        "captureMaxLength",
        "autoRecall",
        "recallPath",
        "recallLimit",
        "recallScoreThreshold",
        "recallMaxContentChars",
        "recallPreferAbstract",
        "recallTokenBudget",
        "adaptiveRecall",
        "recallCacheTtlMs",
        "recallFastMaxAgeMs",
        "recallBackgroundRefresh",
        "recallTierOverrides",
        "commitTokenThreshold",
        "bypassSessionPatterns",
        "emitStandardDiagnostics",
        "logFindRequests",
      ],
      "openviking config",
    );

    const mode = (cfg.mode === "local" || cfg.mode === "remote" ? cfg.mode : "local") as
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
    const configIdentity = readOpenVikingConfigIdentity(configPath);

    const localBaseUrl = `http://127.0.0.1:${port}`;
    const rawBaseUrl =
      mode === "local" ? localBaseUrl : (typeof cfg.baseUrl === "string" ? cfg.baseUrl : resolveDefaultBaseUrl());
    const resolvedBaseUrl = resolveEnvVars(rawBaseUrl).replace(/\/+$/, "");
    const rawApiKey = typeof cfg.apiKey === "string" ? cfg.apiKey : process.env.OPENVIKING_API_KEY;
    const captureMode = cfg.captureMode;
    if (
      typeof captureMode !== "undefined" &&
      captureMode !== "semantic" &&
      captureMode !== "keyword"
    ) {
      throw new Error(`openviking captureMode must be "semantic" or "keyword"`);
    }
    const recallPath = cfg.recallPath;
    if (
      typeof recallPath !== "undefined" &&
      recallPath !== "assemble" &&
      recallPath !== "hook"
    ) {
      throw new Error(`openviking recallPath must be "assemble" or "hook"`);
    }

    return {
      mode,
      configPath,
      port,
      baseUrl: resolvedBaseUrl,
      account: resolveIdentityField(
        cfg.account,
        process.env.OPENVIKING_ACCOUNT ?? process.env.OPENVIKING_ACCOUNT_ID,
        configIdentity.account,
      ),
      user: resolveIdentityField(
        cfg.user,
        process.env.OPENVIKING_USER ?? process.env.OPENVIKING_USER_ID,
        configIdentity.user,
      ),
      agentId: resolveAgentId(cfg.agentId),
      apiKey: rawApiKey ? resolveEnvVars(rawApiKey) : "",
      targetUri: typeof cfg.targetUri === "string" ? cfg.targetUri : DEFAULT_TARGET_URI,
      timeoutMs: Math.max(1000, Math.floor(toNumber(cfg.timeoutMs, DEFAULT_TIMEOUT_MS))),
      autoCapture: cfg.autoCapture !== false,
      captureMode: captureMode ?? DEFAULT_CAPTURE_MODE,
      captureMaxLength: Math.max(
        200,
        Math.min(200_000, Math.floor(toNumber(cfg.captureMaxLength, DEFAULT_CAPTURE_MAX_LENGTH))),
      ),
      autoRecall: cfg.autoRecall !== false,
      recallPath: recallPath ?? DEFAULT_RECALL_PATH,
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
      adaptiveRecall:
        typeof cfg.adaptiveRecall === "boolean"
          ? cfg.adaptiveRecall
          : DEFAULT_ADAPTIVE_RECALL,
      recallCacheTtlMs: Math.max(
        0,
        Math.min(3_600_000, Math.floor(toNumber(cfg.recallCacheTtlMs, DEFAULT_RECALL_CACHE_TTL_MS))),
      ),
      recallFastMaxAgeMs: Math.max(
        0,
        Math.min(3_600_000, Math.floor(toNumber(cfg.recallFastMaxAgeMs, DEFAULT_RECALL_FAST_MAX_AGE_MS))),
      ),
      recallBackgroundRefresh:
        typeof cfg.recallBackgroundRefresh === "boolean"
          ? cfg.recallBackgroundRefresh
          : DEFAULT_RECALL_BACKGROUND_REFRESH,
      recallTierOverrides: toRecallTierOverrides(cfg.recallTierOverrides),
      commitTokenThreshold: Math.max(
        0,
        Math.min(100_000, Math.floor(toNumber(cfg.commitTokenThreshold, DEFAULT_COMMIT_TOKEN_THRESHOLD))),
      ),
      bypassSessionPatterns: toStringArray(
        cfg.bypassSessionPatterns,
        DEFAULT_BYPASS_SESSION_PATTERNS,
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
    account: {
      label: "OpenViking Account",
      placeholder: "from ov.conf default_account",
      help: "Tenant account sent as X-OpenViking-Account. Defaults to ov.conf default_account or OPENVIKING_ACCOUNT.",
    },
    user: {
      label: "OpenViking User",
      placeholder: "from ov.conf default_user",
      help: "Tenant user sent as X-OpenViking-User. Defaults to ov.conf default_user or OPENVIKING_USER.",
    },
    agentId: {
      label: "Agent ID",
      placeholder: "auto-generated",
      help: 'OpenViking X-OpenViking-Agent: non-default values combine with OpenClaw ctx.agentId as "<config>_<sessionAgent>" (then sanitized to [a-zA-Z0-9_-]). Use "default" to send only ctx.agentId.',
    },
    apiKey: {
      label: "OpenViking API Key",
      sensitive: true,
      placeholder: "${OPENVIKING_API_KEY}",
      help: "Optional API key for OpenViking server",
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
    recallPath: {
      label: "Recall Path",
      placeholder: DEFAULT_RECALL_PATH,
      advanced: true,
      help: '"assemble" keeps memory injection inside the context-engine path; "hook" preserves legacy before_prompt_build recall.',
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
    adaptiveRecall: {
      label: "Adaptive Recall",
      advanced: true,
      help: "Skip or cache OpenViking memory recall for mechanical turns while preserving full recall for substantive memory-needed prompts.",
    },
    recallCacheTtlMs: {
      label: "Recall Cache TTL (ms)",
      placeholder: String(DEFAULT_RECALL_CACHE_TTL_MS),
      advanced: true,
      help: "How long exact recall results can be reused for repeated prompts.",
    },
    recallFastMaxAgeMs: {
      label: "Fast Recall Max Age (ms)",
      placeholder: String(DEFAULT_RECALL_FAST_MAX_AGE_MS),
      advanced: true,
      help: "How long a session's latest recall can be reused for short follow-up prompts.",
    },
    recallBackgroundRefresh: {
      label: "Recall Background Refresh",
      advanced: true,
      help: "Refresh recall cache in the background for fast-tier follow-up prompts without blocking the current response.",
    },
    recallTierOverrides: {
      label: "Recall Tier Overrides",
      advanced: true,
      help: "Optional substring overrides: { full: [...], none: [...] }.",
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
