import { getEnv } from "./runtime-utils.js";

export type MemoryOpenVikingConfig = {
  mode?: "remote";
  baseUrl?: string;
  peer_role?: "none" | "assistant" | "person";
  peer_prefix?: string;
  apiKey?: string;
  /** Advanced option. Only needed when explicitly sending tenant identity headers. With a user key the server derives identity from the key. */
  accountId?: string;
  /** Advanced option. Only needed when explicitly sending tenant identity headers. */
  userId?: string;
  targetUri?: string;
  timeoutMs?: number;
  autoCapture?: boolean;
  captureMode?: "semantic" | "keyword";
  captureMaxLength?: number;
  autoRecall?: boolean;
  /** Outer time budget for the whole auto-recall flow, including search, ranking, and reads. */
  autoRecallTimeoutMs?: number;
  /** Include resources in auto-recall and default memory_recall search. Default false. */
  recallResources?: boolean;
  recallLimit?: number;
  recallScoreThreshold?: number;
  /** Maximum total characters injected by auto-recall. */
  recallMaxInjectedChars?: number;
  /** @deprecated Auto-recall no longer truncates individual memories. */
  recallMaxContentChars?: number;
  recallPreferAbstract?: boolean;
  /** @deprecated Use recallMaxInjectedChars. */
  recallTokenBudget?: number;
  commitTokenThreshold?: number;
  /**
   * WM v2: number of most-recent messages to keep live after an afterTurn
   * commit so the next turn still has immediate context. Forwarded to the
   * server as `keep_recent_count`. Default 10. The compact path ignores this
   * value and always passes 0.
   */
  commitKeepRecentCount?: number;
  bypassSessionPatterns?: string[];
  /**
   * When true (default), emit structured `openviking: diag {...}` lines (and any future
   * standard-diagnostics file writes) for assemble/afterTurn. Set false to disable.
   */
  emitStandardDiagnostics?: boolean;
  /** When true, log tenant routing for semantic find and session writes (messages/commit) to the plugin logger. */
  logFindRequests?: boolean;
  /** Agent-visible add_resource tool is disabled by default; manual /add-resource remains available. */
  enableAddResourceTool?: boolean;
  /** Agent-visible tool allowlist. Supports exact tool names or groups such as "memory" and "resource_query". */
  enabledTools?: string[] | string;
  /** Agent-visible tool blocklist applied after enabledTools. Supports exact tool names or groups. */
  disabledTools?: string[] | string;
  agentExperience?: {
    enabled?: boolean;
    recallLimit?: number;
    scoreThreshold?: number;
    maxInjectedChars?: number;
    minQueryChars?: number;
  };
};

/** Runtime config after memoryOpenVikingConfigSchema.parse() has applied defaults. */
export type ParsedMemoryOpenVikingConfig = Required<
  Omit<MemoryOpenVikingConfig, "agentExperience">
> & {
  agentExperience: Required<NonNullable<MemoryOpenVikingConfig["agentExperience"]>>;
};

const DEFAULT_BASE_URL = "http://127.0.0.1:1933";
const DEFAULT_TARGET_URI = "viking://user/memories";
const DEFAULT_TIMEOUT_MS = 15000;
const DEFAULT_CAPTURE_MODE = "semantic";
const DEFAULT_CAPTURE_MAX_LENGTH = 24000;
const DEFAULT_AUTO_RECALL_TIMEOUT_MS = 5000;
const DEFAULT_RECALL_LIMIT = 6;
const DEFAULT_RECALL_SCORE_THRESHOLD = 0.15;
const DEFAULT_RECALL_MAX_CONTENT_CHARS = 5000;
const DEFAULT_RECALL_PREFER_ABSTRACT = false;
const DEFAULT_RECALL_MAX_INJECTED_CHARS = 4000;
const DEFAULT_COMMIT_TOKEN_THRESHOLD = 20000;
const DEFAULT_COMMIT_KEEP_RECENT_COUNT = 10;
const DEFAULT_BYPASS_SESSION_PATTERNS: string[] = [];
const DEFAULT_EMIT_STANDARD_DIAGNOSTICS = false;
const DEFAULT_PEER_ROLE = "none" as const;
const DEFAULT_PEER_PREFIX = "";
export const OPENVIKING_ADD_RESOURCE_TOOL_NAME = "add_resource" as const;
export const OPENVIKING_DEFAULT_ENABLED_TOOL_NAMES = [
  "add_skill",
  "ov_search",
  "ov_read",
  "ov_multi_read",
  "ov_list",
  "memory_recall",
  "memory_store",
  "memory_forget",
  "ov_archive_search",
  "ov_archive_expand",
  "openviking_tool_result_read",
  "openviking_tool_result_search",
  "openviking_tool_result_list",
] as const;
export const OPENVIKING_ALL_TOOL_NAMES = [
  OPENVIKING_ADD_RESOURCE_TOOL_NAME,
  ...OPENVIKING_DEFAULT_ENABLED_TOOL_NAMES,
] as const;
export type OpenVikingToolName = typeof OPENVIKING_ALL_TOOL_NAMES[number];
export const OPENVIKING_TOOL_GROUPS: Record<string, readonly OpenVikingToolName[]> = {
  all: OPENVIKING_ALL_TOOL_NAMES,
  default: OPENVIKING_DEFAULT_ENABLED_TOOL_NAMES,
  memory: ["memory_recall", "memory_store", "memory_forget"],
  resource_query: ["ov_search", "ov_read", "ov_multi_read", "ov_list"],
  import: ["add_resource", "add_skill"],
  archive: ["ov_archive_search", "ov_archive_expand"],
  tool_result: [
    "openviking_tool_result_read",
    "openviking_tool_result_search",
    "openviking_tool_result_list",
  ],
};
const DEFAULT_AGENT_EXPERIENCE = {
  enabled: false,
  recallLimit: 3,
  scoreThreshold: 0.35,
  maxInjectedChars: 6000,
  minQueryChars: 12,
};

function resolvePeerPrefix(configured: unknown): string {
  if (typeof configured === "string" && configured.trim()) {
    const trimmed = configured.trim();
    return trimmed === "default" ? DEFAULT_PEER_PREFIX : trimmed;
  }
  return DEFAULT_PEER_PREFIX;
}

function resolvePeerRole(configured: unknown) {
  if (typeof configured === "string") {
    const role = configured.trim().toLowerCase();
    if (role === "none" || role === "assistant" || role === "person") {
      return role;
    }
    throw new Error(`openviking peer_role must be "none", "assistant", or "person"`);
  }
  if (configured !== undefined) {
    throw new Error(`openviking peer_role must be "none", "assistant", or "person"`);
  }
  return DEFAULT_PEER_ROLE;
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

function toRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function expandToolSelectors(value: unknown, fallback: string[], label: string): OpenVikingToolName[] {
  const entries = toStringArray(value, fallback);
  const seen = new Set<OpenVikingToolName>();
  const normalized: OpenVikingToolName[] = [];
  const unknown: string[] = [];

  for (const rawEntry of entries) {
    const entry = rawEntry.trim();
    const group = OPENVIKING_TOOL_GROUPS[entry];
    const tools = group ??
      ((OPENVIKING_ALL_TOOL_NAMES as readonly string[]).includes(entry)
        ? [entry as OpenVikingToolName]
        : undefined);
    if (!tools) {
      unknown.push(entry);
      continue;
    }
    for (const tool of tools) {
      if (!seen.has(tool)) {
        seen.add(tool);
        normalized.push(tool);
      }
    }
  }

  if (unknown.length > 0) {
    throw new Error(`openviking ${label} contains unknown tool selectors: ${unknown.join(", ")}`);
  }
  return normalized;
}

function normalizeEnabledTools(cfg: Record<string, unknown>): {
  enabledTools: OpenVikingToolName[];
  disabledTools: OpenVikingToolName[];
} {
  const enableAddResourceTool = cfg.enableAddResourceTool === true;
  const defaultTools = enableAddResourceTool
    ? [OPENVIKING_ADD_RESOURCE_TOOL_NAME, ...OPENVIKING_DEFAULT_ENABLED_TOOL_NAMES]
    : [...OPENVIKING_DEFAULT_ENABLED_TOOL_NAMES];
  const selected = expandToolSelectors(cfg.enabledTools, defaultTools, "enabledTools");
  const disabled = expandToolSelectors(cfg.disabledTools, [], "disabledTools");
  const disabledSet = new Set(disabled);
  if (!enableAddResourceTool) {
    disabledSet.add(OPENVIKING_ADD_RESOURCE_TOOL_NAME);
  }
  const enabledTools = selected.filter((tool) =>
    !disabledSet.has(tool) &&
    (tool !== OPENVIKING_ADD_RESOURCE_TOOL_NAME || enableAddResourceTool)
  );

  return {
    enabledTools,
    disabledTools: Array.from(disabledSet),
  };
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
  parse(value: unknown): ParsedMemoryOpenVikingConfig {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      value = {};
    }
    const cfg = value as Record<string, unknown>;
    assertAllowedKeys(
      cfg,
      [
        "mode",
        "baseUrl",
        "peer_role",
        "peer_prefix",
        "serverAuthMode",
        "apiKey",
        "accountId",
        "userId",
        "targetUri",
        "timeoutMs",
        "autoCapture",
        "captureMode",
        "captureMaxLength",
        "autoRecall",
        "autoRecallTimeoutMs",
        "recallResources",
        "recallLimit",
        "recallScoreThreshold",
        "recallMaxInjectedChars",
        "recallMaxContentChars",
        "recallPreferAbstract",
        "recallTokenBudget",
        "commitTokenThreshold",
        "commitKeepRecentCount",
        "bypassSessionPatterns",
        "ingestReplyAssist",
        "ingestReplyAssistMinSpeakerTurns",
        "ingestReplyAssistMinChars",
        "ingestReplyAssistIgnoreSessionPatterns",
        "emitStandardDiagnostics",
        "logFindRequests",
        "enableAddResourceTool",
        "enabledTools",
        "disabledTools",
        "agentExperience",
      ],
      "openviking config",
    );
    const agentExperienceRaw = toRecord(cfg.agentExperience);
    assertAllowedKeys(
      agentExperienceRaw,
      ["enabled", "recallLimit", "scoreThreshold", "maxInjectedChars", "minQueryChars"],
      "openviking config agentExperience",
    );

    const mode = "remote" as const;
    const peerRole = resolvePeerRole(cfg.peer_role);
    const peerPrefix = resolvePeerPrefix(cfg.peer_prefix);
    const rawBaseUrl = typeof cfg.baseUrl === "string" ? cfg.baseUrl : resolveDefaultBaseUrl();
    const resolvedBaseUrl = resolveEnvVars(rawBaseUrl).replace(/\/+$/, "");
    const rawApiKey = typeof cfg.apiKey === "string" ? cfg.apiKey : getEnv("OPENVIKING_API_KEY");
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
        : (getEnv("OPENVIKING_ACCOUNT_ID")?.trim() || "");
    const userId =
      typeof cfg.userId === "string" && cfg.userId.trim()
        ? cfg.userId.trim()
        : (getEnv("OPENVIKING_USER_ID")?.trim() || "");

    const recallMaxInjectedChars = Math.max(
      100,
      Math.min(
        50000,
        Math.floor(
          toNumber(
            cfg.recallMaxInjectedChars,
            toNumber(cfg.recallTokenBudget, DEFAULT_RECALL_MAX_INJECTED_CHARS),
          ),
        ),
      ),
    );
    const { enabledTools, disabledTools } = normalizeEnabledTools(cfg);

    return {
      mode,
      baseUrl: resolvedBaseUrl,
      peer_role: peerRole,
      peer_prefix: peerPrefix,
      apiKey: rawApiKey ? resolveEnvVars(rawApiKey) : "",
      accountId,
      userId,
      targetUri: typeof cfg.targetUri === "string" ? cfg.targetUri : DEFAULT_TARGET_URI,
      timeoutMs: Math.max(1000, Math.floor(toNumber(cfg.timeoutMs, DEFAULT_TIMEOUT_MS))),
      autoCapture: cfg.autoCapture !== false,
      captureMode: captureMode ?? DEFAULT_CAPTURE_MODE,
      captureMaxLength: Math.max(
        200,
        Math.min(200_000, Math.floor(toNumber(cfg.captureMaxLength, DEFAULT_CAPTURE_MAX_LENGTH))),
      ),
      autoRecall: cfg.autoRecall !== false,
      autoRecallTimeoutMs: Math.max(
        1000,
        Math.min(300_000, Math.floor(toNumber(cfg.autoRecallTimeoutMs, DEFAULT_AUTO_RECALL_TIMEOUT_MS))),
      ),
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
      recallPreferAbstract:
        typeof cfg.recallPreferAbstract === "boolean"
          ? cfg.recallPreferAbstract
          : DEFAULT_RECALL_PREFER_ABSTRACT,
      recallMaxInjectedChars,
      recallTokenBudget: recallMaxInjectedChars,
      commitTokenThreshold: Math.max(
        0,
        Math.min(100_000, Math.floor(toNumber(cfg.commitTokenThreshold, DEFAULT_COMMIT_TOKEN_THRESHOLD))),
      ),
      commitKeepRecentCount: Math.max(
        0,
        Math.min(
          1_000,
          Math.floor(toNumber(cfg.commitKeepRecentCount, DEFAULT_COMMIT_KEEP_RECENT_COUNT)),
        ),
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
      enableAddResourceTool: cfg.enableAddResourceTool === true,
      enabledTools,
      disabledTools,
      agentExperience: {
        enabled:
          typeof agentExperienceRaw.enabled === "boolean"
            ? agentExperienceRaw.enabled
            : DEFAULT_AGENT_EXPERIENCE.enabled,
        recallLimit: Math.max(
          1,
          Math.min(
            10,
            Math.floor(toNumber(agentExperienceRaw.recallLimit, DEFAULT_AGENT_EXPERIENCE.recallLimit)),
          ),
        ),
        scoreThreshold: Math.min(
          1,
          Math.max(0, toNumber(agentExperienceRaw.scoreThreshold, DEFAULT_AGENT_EXPERIENCE.scoreThreshold)),
        ),
        maxInjectedChars: Math.max(
          500,
          Math.min(
            50_000,
            Math.floor(toNumber(agentExperienceRaw.maxInjectedChars, DEFAULT_AGENT_EXPERIENCE.maxInjectedChars)),
          ),
        ),
        minQueryChars: Math.max(
          1,
          Math.min(
            500,
            Math.floor(toNumber(agentExperienceRaw.minQueryChars, DEFAULT_AGENT_EXPERIENCE.minQueryChars)),
          ),
        ),
      },
    };
  },
  uiHints: {
    baseUrl: {
      label: "OpenViking Base URL",
      placeholder: DEFAULT_BASE_URL,
      help: "HTTP URL when mode is remote (or use ${OPENVIKING_BASE_URL})",
    },
    peer_role: {
      label: "Peer Role",
      placeholder: DEFAULT_PEER_ROLE,
      help: 'Controls which session messages get peer_id: "none", "assistant", or "person".',
    },
    peer_prefix: {
      label: "Peer Prefix",
      placeholder: "optional-prefix",
      help: "Optional prefix applied to assistant peer_id values derived from OpenClaw runtime agent IDs.",
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
      help: "Advanced option. Tenant account ID. Only needed when explicitly sending identity headers, such as root-key or trusted deployments. With a user key the server derives identity from the key.",
      advanced: true,
    },
    userId: {
      label: "User ID",
      placeholder: "(derived from API key)",
      help: "Advanced option. Tenant user ID. Only needed when explicitly sending identity headers.",
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
    autoRecallTimeoutMs: {
      label: "Auto-Recall Timeout (ms)",
      placeholder: String(DEFAULT_AUTO_RECALL_TIMEOUT_MS),
      advanced: true,
      help: "Outer time budget for the whole auto-recall flow, including search, ranking, and memory reads.",
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
    recallMaxInjectedChars: {
      label: "Recall Max Injected Chars",
      placeholder: String(DEFAULT_RECALL_MAX_INJECTED_CHARS),
      advanced: true,
      help: "Maximum total characters for auto-recall memory injection. Complete memories that do not fit are skipped, not truncated.",
    },
    recallMaxContentChars: {
      label: "Deprecated Recall Max Content Chars",
      placeholder: String(DEFAULT_RECALL_MAX_CONTENT_CHARS),
      advanced: true,
      help: "Deprecated compatibility option and will be removed in a future release. Auto-recall now keeps individual memories intact and uses recallMaxInjectedChars.",
    },
    recallPreferAbstract: {
      label: "Recall Prefer Abstract",
      advanced: true,
      help: "Use memory abstract instead of fetching full content when abstract is available. Reduces token usage.",
    },
    recallTokenBudget: {
      label: "Deprecated Recall Token Budget",
      placeholder: String(DEFAULT_RECALL_MAX_INJECTED_CHARS),
      advanced: true,
      help: "Deprecated compatibility alias and will be removed in a future release. Use recallMaxInjectedChars.",
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
    commitKeepRecentCount: {
      label: "Commit Keep Recent Count",
      placeholder: String(DEFAULT_COMMIT_KEEP_RECENT_COUNT),
      advanced: true,
      help:
        "Number of most-recent messages to keep live after an afterTurn commit. " +
        "Forwarded as keep_recent_count to the server. Compact path always uses 0.",
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
        "Or set env OPENVIKING_LOG_ROUTING=1 or OPENVIKING_DEBUG=1 (no JSON edit).",
      advanced: true,
    },
    enableAddResourceTool: {
      label: "Enable Add Resource Tool",
      placeholder: "false",
      help: "Disabled by default so search and read flows cannot call add_resource. Set true only when agents should import resources; manual /add-resource remains available.",
      advanced: true,
    },
    enabledTools: {
      label: "Enabled Tools",
      placeholder: "default",
      help: "Agent-visible tool allowlist. Accepts tool names or groups: default, all, memory, resource_query, import, archive, tool_result. add_resource also requires enableAddResourceTool=true.",
      advanced: true,
    },
    disabledTools: {
      label: "Disabled Tools",
      placeholder: "memory",
      help: "Agent-visible tool blocklist applied after enabledTools. Accepts the same tool names or groups.",
      advanced: true,
    },
  },
};
