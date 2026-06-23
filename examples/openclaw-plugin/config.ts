import { homedir } from "node:os";

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
  /**
   * Auto-commit threshold expressed as a fraction (0-1) of the model context
   * window (tokenBudget). afterTurn triggers an async commit once estimated
   * pending tokens reach `commitTokenThresholdRatio * tokenBudget`
   * (e.g. 0.5 = 50% of the window). Replaces the former absolute
   * `commitTokenThreshold` (still accepted but ignored for backward
   * compatibility). Set to 0 to commit every turn.
   */
  commitTokenThresholdRatio?: number;
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
  /** Enable recall trace recording. Default false. */
  traceRecall?: boolean;
  /** Persist recall traces to local JSONL files. Default false. */
  traceRecallPersist?: boolean;
  /** Directory for JSONL recall trace files. */
  traceRecallDir?: string;
  /** Number of days to retain persisted trace files. */
  traceRecallRetentionDays?: number;
  /** Number of recent persisted days to preload on startup. */
  traceRecallLoadRecentDays?: number;
  /** Maximum in-memory recall trace entries. */
  traceRecallMaxEntries?: number;
  /** Maximum candidate results stored per search in trace. */
  traceRecallMaxResultsPerSearch?: number;
  /** Preview character limit for persisted trace summaries. */
  traceRecallPreviewChars?: number;
  /** Maximum query characters preserved in trace. */
  traceRecallQueryMaxChars?: number;
  /** Maximum days to scan when querying persisted traces without explicit time bounds. */
  traceRecallQueryMaxDays?: number;
  /** Whether trace queries include full content by default. */
  traceRecallIncludeContentByDefault?: boolean;
  /** Whether raw user text preview may be persisted. Default false. */
  traceRecallIncludeRawUserPreview?: boolean;
  /** Auto-recall target resource types. Empty means the backward-compatible memory recall set. */
  recallTargetTypes?: Array<"resource" | "user" | "agent"> | string;
  /** Agent-visible add_resource tool is disabled by default; manual /add-resource remains available. */
  enableAddResourceTool?: boolean;
  /** Agent-visible tool allowlist. Supports exact tool names or groups such as "memory" and "resource_query". */
  enabledTools?: string[] | string;
  /** Agent-visible tool blocklist applied after enabledTools. Supports exact tool names or groups. */
  disabledTools?: string[] | string;
  /** Optional JSON file path for runtime query config overrides. Empty means in-memory only. */
  runtimeQueryConfigPath?: string;
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
  Omit<MemoryOpenVikingConfig, "agentExperience" | "recallTargetTypes">
> & {
  agentExperience: Required<NonNullable<MemoryOpenVikingConfig["agentExperience"]>>;
  recallTargetTypes: Array<"resource" | "user" | "agent">;
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
const DEFAULT_COMMIT_TOKEN_THRESHOLD_RATIO = 0.5;
const DEFAULT_COMMIT_KEEP_RECENT_COUNT = 10;
const DEFAULT_BYPASS_SESSION_PATTERNS: string[] = [];
const DEFAULT_EMIT_STANDARD_DIAGNOSTICS = false;
const DEFAULT_PEER_ROLE = "assistant" as const;
const DEFAULT_PEER_PREFIX = "";
const DEFAULT_TRACE_RECALL_DIR = "~/.openclaw/openviking/recall-traces";
const DEFAULT_TRACE_RECALL_RETENTION_DAYS = 14;
const DEFAULT_TRACE_RECALL_LOAD_RECENT_DAYS = 2;
const DEFAULT_TRACE_RECALL_MAX_ENTRIES = 1000;
const DEFAULT_TRACE_RECALL_MAX_RESULTS_PER_SEARCH = 20;
const DEFAULT_TRACE_RECALL_PREVIEW_CHARS = 240;
const DEFAULT_TRACE_RECALL_QUERY_MAX_CHARS = 4000;
const DEFAULT_TRACE_RECALL_QUERY_MAX_DAYS = 14;
const ALLOWED_RECALL_TARGET_TYPES = ["resource", "user", "agent"] as const;
const DEFAULT_RECALL_TARGET_TYPES = ["user", "agent"] as const;
type RecallTargetType = typeof ALLOWED_RECALL_TARGET_TYPES[number];
export const OPENVIKING_ADD_RESOURCE_TOOL_NAME = "add_resource" as const;
export const OPENVIKING_DEFAULT_ENABLED_TOOL_NAMES = [
  "add_skill",
  "ov_search",
  "ov_read",
  "ov_multi_read",
  "ov_list",
  "memory_recall",
  "ov_recall_trace",
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
  recall_trace: ["ov_recall_trace"],
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

function expandHomeDir(value: string): string {
  if (value === "~") {
    return homedir();
  }
  if (value.startsWith("~/")) {
    return `${homedir()}${value.slice(1)}`;
  }
  return value;
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

function toIntegerInRange(value: unknown, fallback: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, Math.floor(toNumber(value, fallback))));
}

function normalizeRecallTargetTypes(value: unknown, includeResources = false): RecallTargetType[] {
  const entries = toStringArray(value, [...DEFAULT_RECALL_TARGET_TYPES]);
  const seen = new Set<RecallTargetType>();
  const normalized: RecallTargetType[] = [];
  const unknown: string[] = [];

  for (const entry of entries) {
    if ((ALLOWED_RECALL_TARGET_TYPES as readonly string[]).includes(entry)) {
      const typed = entry as RecallTargetType;
      if (!seen.has(typed)) {
        seen.add(typed);
        normalized.push(typed);
      }
    } else {
      unknown.push(entry);
    }
  }

  if (unknown.length > 0) {
    throw new Error(`openviking recallTargetTypes contains unknown resource types: ${unknown.join(", ")}`);
  }

  const result = normalized.length > 0 ? normalized : [...DEFAULT_RECALL_TARGET_TYPES];
  if (includeResources && !seen.has("resource")) {
    result.push("resource");
  }
  return result;
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
        "commitTokenThresholdRatio",
        "commitKeepRecentCount",
        "bypassSessionPatterns",
        "ingestReplyAssist",
        "ingestReplyAssistMinSpeakerTurns",
        "ingestReplyAssistMinChars",
        "ingestReplyAssistIgnoreSessionPatterns",
        "emitStandardDiagnostics",
        "logFindRequests",
        "traceRecall",
        "traceRecallPersist",
        "traceRecallDir",
        "traceRecallRetentionDays",
        "traceRecallLoadRecentDays",
        "traceRecallMaxEntries",
        "traceRecallMaxResultsPerSearch",
        "traceRecallPreviewChars",
        "traceRecallQueryMaxChars",
        "traceRecallQueryMaxDays",
        "traceRecallIncludeContentByDefault",
        "traceRecallIncludeRawUserPreview",
        "recallTargetTypes",
        "enableAddResourceTool",
        "enabledTools",
        "disabledTools",
        "runtimeQueryConfigPath",
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
    const recallResources = cfg.recallResources === true || envFlag("OPENVIKING_RECALL_RESOURCES");
    const recallTargetTypes = normalizeRecallTargetTypes(
      cfg.recallTargetTypes,
      !("recallTargetTypes" in cfg) && recallResources,
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
      recallResources,
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
      commitTokenThresholdRatio: Math.max(
        0,
        Math.min(1, toNumber(cfg.commitTokenThresholdRatio, DEFAULT_COMMIT_TOKEN_THRESHOLD_RATIO)),
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
      traceRecall: cfg.traceRecall === true,
      traceRecallPersist: cfg.traceRecallPersist === true,
      traceRecallDir:
        typeof cfg.traceRecallDir === "string" && cfg.traceRecallDir.trim()
          ? expandHomeDir(cfg.traceRecallDir.trim())
          : expandHomeDir(DEFAULT_TRACE_RECALL_DIR),
      traceRecallRetentionDays: toIntegerInRange(
        cfg.traceRecallRetentionDays,
        DEFAULT_TRACE_RECALL_RETENTION_DAYS,
        1,
        3650,
      ),
      traceRecallLoadRecentDays: toIntegerInRange(
        cfg.traceRecallLoadRecentDays,
        DEFAULT_TRACE_RECALL_LOAD_RECENT_DAYS,
        0,
        3650,
      ),
      traceRecallMaxEntries: toIntegerInRange(
        cfg.traceRecallMaxEntries,
        DEFAULT_TRACE_RECALL_MAX_ENTRIES,
        1,
        1_000_000,
      ),
      traceRecallMaxResultsPerSearch: toIntegerInRange(
        cfg.traceRecallMaxResultsPerSearch,
        DEFAULT_TRACE_RECALL_MAX_RESULTS_PER_SEARCH,
        1,
        1_000,
      ),
      traceRecallPreviewChars: toIntegerInRange(
        cfg.traceRecallPreviewChars,
        DEFAULT_TRACE_RECALL_PREVIEW_CHARS,
        20,
        10_000,
      ),
      traceRecallQueryMaxChars: toIntegerInRange(
        cfg.traceRecallQueryMaxChars,
        DEFAULT_TRACE_RECALL_QUERY_MAX_CHARS,
        200,
        200_000,
      ),
      traceRecallQueryMaxDays: toIntegerInRange(
        cfg.traceRecallQueryMaxDays,
        DEFAULT_TRACE_RECALL_QUERY_MAX_DAYS,
        1,
        3650,
      ),
      traceRecallIncludeContentByDefault: cfg.traceRecallIncludeContentByDefault === true,
      traceRecallIncludeRawUserPreview: cfg.traceRecallIncludeRawUserPreview === true,
      recallTargetTypes,
      enableAddResourceTool: cfg.enableAddResourceTool === true,
      enabledTools,
      disabledTools,
      runtimeQueryConfigPath:
        typeof cfg.runtimeQueryConfigPath === "string" && cfg.runtimeQueryConfigPath.trim()
          ? expandHomeDir(cfg.runtimeQueryConfigPath.trim())
          : "",
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
    recallTargetTypes: {
      label: "Recall Target Types",
      placeholder: "user,agent",
      help: "Comma-separated auto-recall and default memory_recall targets: user, agent, resource. Session history is available through ov_archive_search and ov_archive_expand.",
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
    commitTokenThresholdRatio: {
      label: "Commit Token Threshold Ratio",
      placeholder: String(DEFAULT_COMMIT_TOKEN_THRESHOLD_RATIO),
      advanced: true,
      help: "Auto-commit triggers once estimated pending tokens reach this fraction (0-1) of the model context window (e.g. 0.5 = 50%). Set to 0 to commit every turn.",
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
    traceRecall: {
      label: "Trace Recall",
      placeholder: "false",
      help: "Enable best-effort recall trace recording for debugging recall and search decisions.",
      advanced: true,
    },
    traceRecallPersist: {
      label: "Persist Recall Trace",
      placeholder: "false",
      help: "Persist recall traces to local JSONL files. Disabled by default.",
      advanced: true,
    },
    traceRecallDir: {
      label: "Recall Trace Directory",
      placeholder: DEFAULT_TRACE_RECALL_DIR,
      help: "Directory for persisted recall trace JSONL files.",
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
      help: "Agent-visible tool allowlist. Accepts tool names or groups: default, all, memory, resource_query, import, recall_trace, archive, tool_result. add_resource also requires enableAddResourceTool=true.",
      advanced: true,
    },
    disabledTools: {
      label: "Disabled Tools",
      placeholder: "memory",
      help: "Agent-visible tool blocklist applied after enabledTools. Accepts the same tool names or groups.",
      advanced: true,
    },
    runtimeQueryConfigPath: {
      label: "Runtime Query Config Path",
      placeholder: "~/.openclaw/openviking/runtime-query-config.json",
      help: "Optional JSON file for /ov-query-config runtime overrides. Empty keeps overrides in memory only.",
      advanced: true,
    },
  },
};
