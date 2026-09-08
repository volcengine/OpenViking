// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
/**
 * Every knob a memory plugin reads, declared once.
 *
 * A setting stayed uniform across harnesses only when a shared module read it
 * on the hot path: `recallLimit`, `scoreThreshold` and `captureMaxLength` agree
 * everywhere because `recall-core` and `capture-utils` read them. Everything
 * else drifted — the same switch is `autoRecall` in three loaders, an
 * `autoRecall` *object* in opencode, `syncTurns` in dsh and pi, and absent from
 * the thin hook runtime — because each loader hand-wrote its own list and the
 * author picked a name. Three more lists then had to be kept in step by hand:
 * the doctor's known-knob set, the workspace file's dotted-key map, and the
 * implicit inventory inside `loadAgentHookConfig`.
 *
 * This module is the one list. The others are projections of it:
 * `pluginConfigKeys()` for the doctor, `WORKSPACE_KNOB_MAP` for the workspace
 * file, and `resolveKnobs()` for every loader. A knob added here reaches all of
 * them; a knob spelled wrong reaches none.
 *
 * Declaration fields:
 *   name        canonical key, and the property the loaders read
 *   type        bool | int | number | enum | string | list
 *   default     value when no layer supplies one
 *   harness     per-harness default, where one genuinely differs
 *   min/max     clamped, not rejected: a config file must not kill a hook
 *   values      the enum's members; anything else falls back to the default
 *   env         OPENVIKING_* variable, the highest-priority layer
 *   aliases     older spellings, honoured forever; the doctor names them
 *   workspace   dotted key in `.openviking/config.json`
 *   capability  which capability owns it, for the switches in recall/capture
 *   sendOnlyWhenConfigured
 *               the request omits this field unless a layer actually set it,
 *               so the server's own default stands. Loaders project the flag
 *               as `<name>Configured` and `recall-core` reads it from there.
 */

export const CAPABILITIES = ["connection", "peer", "recall", "capture", "session", "debug"];

/** @type {ReadonlyArray<object>} */
export const KNOBS = [
  // ── connection and identity ────────────────────────────────────────────
  // Credentials resolve through `credentials.mjs`; these entries exist so the
  // doctor recognises them inside `plugin` and so ov.conf's harness section
  // keeps working.
  { name: "enabled", type: "bool", default: true, env: "OPENVIKING_MEMORY_ENABLED", capability: "connection" },
  { name: "apiKey", type: "string", default: "", capability: "connection" },
  { name: "accountId", type: "string", default: "", capability: "connection" },
  { name: "userId", type: "string", default: "", capability: "connection" },
  { name: "authMode", type: "enum", values: ["trusted", "api_key"], default: "", aliases: ["auth_mode"], capability: "connection" },
  {
    name: "timeoutMs",
    type: "int",
    default: 15000,
    // opencode drives an editor session that tolerates a slower call; dsh runs
    // inside a chat host that does not.
    harness: { opencode: 30000, dsh: 10000 },
    min: 1000,
    max: 300000,
    env: "OPENVIKING_TIMEOUT_MS",
    aliases: ["requestTimeoutMs"],
    capability: "connection",
  },
  { name: "mcpEnabled", type: "bool", default: true, capability: "connection" },
  { name: "mcpToolCallTimeoutMs", type: "int", default: 60000, min: 1000, max: 600000, capability: "connection" },
  { name: "dataDir", type: "string", default: "", capability: "connection" },

  { name: "peerId", type: "string", default: "", env: "OPENVIKING_PEER_ID", aliases: ["peer_id"], workspace: "peer.id", capability: "peer" },
  { name: "peerSource", type: "template", default: "", env: "OPENVIKING_PEER_SOURCE", workspace: "peer.source", capability: "peer" },
  { name: "workspacePeer", type: "bool", default: true, env: "OPENVIKING_WORKSPACE_PEER", capability: "peer" },

  // ── recall ────────────────────────────────────────────────────────────
  {
    name: "autoRecall",
    type: "bool",
    default: true,
    env: "OPENVIKING_AUTO_RECALL",
    workspace: "recall.enabled",
    capability: "recall",
  },
  { name: "recallLimit", type: "int", default: 10, min: 1, max: 50, env: "OPENVIKING_RECALL_LIMIT", workspace: "recall.max_items", sendOnlyWhenConfigured: true, capability: "recall" },
  { name: "scoreThreshold", type: "number", default: 0.35, min: 0, max: 1, env: "OPENVIKING_SCORE_THRESHOLD", aliases: ["recallScoreThreshold"], workspace: "recall.score_threshold", capability: "recall" },
  { name: "minQueryLength", type: "int", default: 3, min: 1, max: 64, env: "OPENVIKING_MIN_QUERY_LENGTH", aliases: ["recallMinQueryLength"], capability: "recall" },
  { name: "recallTokenBudget", type: "int", default: 2000, min: 200, max: 50000, env: "OPENVIKING_RECALL_TOKEN_BUDGET", aliases: ["recallBudget"], capability: "recall" },
  { name: "recallMaxContentChars", type: "int", default: 500, min: 100, max: 5000, env: "OPENVIKING_RECALL_MAX_CONTENT_CHARS", capability: "recall" },
  { name: "recallPreferAbstract", type: "bool", default: true, env: "OPENVIKING_RECALL_PREFER_ABSTRACT", capability: "recall" },
  { name: "recallPeerScope", type: "enum", values: ["all", "actor"], default: "all", env: "OPENVIKING_RECALL_PEER_SCOPE", workspace: "recall.peer_scope", capability: "recall" },
  { name: "recallDedupTurns", type: "int", default: 5, min: 0, max: 20, env: "OPENVIKING_RECALL_DEDUP_TURNS", workspace: "recall.dedup_turns", capability: "recall" },
  { name: "recallMaxTokens", type: "int", default: 1600, min: 64, max: 200000, env: "OPENVIKING_RECALL_MAX_TOKENS", sendOnlyWhenConfigured: true, capability: "recall" },
  { name: "recallQueryExpansion", type: "enum", values: ["auto", "off"], default: "auto", env: "OPENVIKING_RECALL_QUERY_EXPANSION", sendOnlyWhenConfigured: true, capability: "recall" },
  { name: "recallTimeoutMs", type: "int", default: 120000, min: 1000, max: 600000, env: "OPENVIKING_RECALL_TIMEOUT_MS", capability: "recall" },
  // 0 keeps the built-in default, which outlasts the server's rewrite fuse.
  { name: "recallContextTimeoutMs", type: "int", default: 0, min: 0, max: 600000, env: "OPENVIKING_RECALL_CONTEXT_TIMEOUT_MS", capability: "recall" },
  { name: "logRankingDetails", type: "bool", default: false, env: "OPENVIKING_LOG_RANKING_DETAILS", capability: "recall" },
  { name: "recallLedger", type: "bool", default: true, env: "OPENVIKING_RECALL_LEDGER", capability: "recall" },

  // Digest compression. Claude Code reads this as the tri-state
  // off/client/server/auto through `normalizeRewriteMode`; Codex reads the same
  // key as on/off. Both spellings stay declared so neither harness's users are
  // told their config is a typo.
  { name: "recallCompress", type: "string", default: "auto", env: "OPENVIKING_RECALL_COMPRESS", aliases: ["recallRewrite"], capability: "recall" },
  { name: "recallCompressModel", type: "string", default: "", env: "OPENVIKING_RECALL_COMPRESS_MODEL", capability: "recall" },
  { name: "recallCompressBaseUrl", type: "string", default: "", env: "OPENVIKING_RECALL_COMPRESS_BASE_URL", capability: "recall" },
  { name: "recallCompressThinking", type: "string", default: "", env: "OPENVIKING_RECALL_COMPRESS_THINKING", aliases: ["recallCompressReasoningEffort"], capability: "recall" },
  // 0 means "derive from recallTimeoutMs" — the loader that owns the fallback
  // is the one that knows the request budget it has to fit inside.
  { name: "recallCompressTimeoutMs", type: "int", default: 0, min: 0, max: 600000, env: "OPENVIKING_RECALL_COMPRESS_TIMEOUT_MS", capability: "recall" },
  { name: "recallCompressDetectOnStartup", type: "bool", default: true, env: "OPENVIKING_RECALL_COMPRESS_DETECT_ON_STARTUP", capability: "recall" },
  { name: "recallCompressDetectTimeoutMs", type: "int", default: 15000, min: 1000, max: 600000, env: "OPENVIKING_RECALL_COMPRESS_DETECT_TIMEOUT_MS", capability: "recall" },
  { name: "recallCompressDetectTtlMs", type: "int", default: 604800000, min: 0, capability: "recall", env: "OPENVIKING_RECALL_COMPRESS_DETECT_TTL_MS" },
  { name: "recallCompressMinInputChars", type: "int", default: 1500, min: 0, max: 100000, env: "OPENVIKING_RECALL_COMPRESS_MIN_INPUT_CHARS", capability: "recall" },
  { name: "recallCompressMaxInputChars", type: "int", default: 18000, min: 1000, max: 200000, env: "OPENVIKING_RECALL_COMPRESS_MAX_INPUT_CHARS", capability: "recall" },
  { name: "recallCompressMaxBullets", type: "int", default: 6, min: 1, max: 50, env: "OPENVIKING_RECALL_COMPRESS_MAX_BULLETS", sendOnlyWhenConfigured: true, capability: "recall" },

  // ── capture ───────────────────────────────────────────────────────────
  // `syncTurns` is dsh and pi's spelling of the same switch. It stays an alias
  // rather than a second knob so `ov config switch` moves both.
  {
    name: "autoCapture",
    type: "bool",
    default: true,
    env: "OPENVIKING_AUTO_CAPTURE",
    aliases: ["syncTurns"],
    workspace: "capture.enabled",
    capability: "capture",
  },
  { name: "captureMode", type: "enum", values: ["semantic", "keyword"], default: "semantic", env: "OPENVIKING_CAPTURE_MODE", capability: "capture" },
  { name: "captureMaxLength", type: "int", default: 24000, min: 200, max: 100000, env: "OPENVIKING_CAPTURE_MAX_LENGTH", capability: "capture" },
  // Tool output is reported verbatim; the server owns truncation via
  // tool_output_externalization. This cap only guards pathological payloads.
  { name: "captureToolMaxChars", type: "int", default: 1000000, min: 200, max: 1000000, env: "OPENVIKING_CAPTURE_TOOL_MAX_CHARS", capability: "capture" },
  // Default true: a memory plugin that only sees the user's half of the
  // conversation extracts noticeably worse.
  { name: "captureAssistantTurns", type: "bool", default: true, env: "OPENVIKING_CAPTURE_ASSISTANT_TURNS", capability: "capture" },
  { name: "captureLastAssistantOnStop", type: "bool", default: true, env: "OPENVIKING_CAPTURE_LAST_ASSISTANT_ON_STOP", capability: "capture" },
  { name: "captureToolResults", type: "bool", default: false, env: "OPENVIKING_CAPTURE_TOOL_RESULTS", capability: "capture" },
  // 0 means "derive from timeoutMs": a write gets a longer budget than a read.
  { name: "captureTimeoutMs", type: "int", default: 0, min: 0, max: 600000, env: "OPENVIKING_CAPTURE_TIMEOUT_MS", capability: "capture" },
  { name: "commitTokenThreshold", type: "int", default: 20000, min: 1000, max: 1000000, env: "OPENVIKING_COMMIT_TOKEN_THRESHOLD", workspace: "capture.commit_token_threshold", capability: "capture" },
  { name: "commitKeepRecentCount", type: "int", default: 10, min: 0, max: 1000, env: "OPENVIKING_COMMIT_KEEP_RECENT_COUNT", capability: "capture" },
  { name: "commitTurnThreshold", type: "int", default: 8, min: 1, max: 1000, env: "OPENVIKING_COMMIT_TURN_THRESHOLD", capability: "capture" },
  { name: "autoCommitOnCompact", type: "bool", default: true, env: "OPENVIKING_AUTO_COMMIT_ON_COMPACT", capability: "capture" },
  { name: "writePathAsync", type: "bool", default: true, env: "OPENVIKING_WRITE_PATH_ASYNC", capability: "capture" },

  // ── session lifecycle ─────────────────────────────────────────────────
  { name: "noAutoInject", type: "bool", default: false, env: "OPENVIKING_NO_AUTO_INJECT", capability: "session" },
  { name: "profileTokenBudget", type: "int", default: 10000, min: 500, max: 50000, env: "OPENVIKING_PROFILE_TOKEN_BUDGET", aliases: ["profileBudget"], capability: "session" },
  { name: "resumeContextBudget", type: "int", default: 32000, min: 1024, max: 128000, env: "OPENVIKING_RESUME_CONTEXT_BUDGET", capability: "session" },
  { name: "resumeArchiveInject", type: "bool", default: true, env: "OPENVIKING_RESUME_ARCHIVE_INJECT", capability: "session" },
  { name: "resumeArchiveTokenBudget", type: "int", default: 32000, min: 0, max: 128000, env: "OPENVIKING_RESUME_ARCHIVE_TOKEN_BUDGET", capability: "session" },
  { name: "resumeArchiveMaxChars", type: "int", default: 6000, min: 1000, max: 200000, env: "OPENVIKING_RESUME_ARCHIVE_MAX_CHARS", capability: "session" },
  { name: "skillExperience", type: "bool", default: false, env: "OPENVIKING_SKILL_EXPERIENCE", capability: "session" },
  { name: "skillExperienceLimit", type: "int", default: 3, min: 1, max: 50, env: "OPENVIKING_SKILL_EXPERIENCE_LIMIT", capability: "session" },
  { name: "skipSubagentSessions", type: "bool", default: false, env: "OPENVIKING_SKIP_SUBAGENT_SESSIONS", capability: "session" },
  { name: "repoContext", type: "bool", default: true, capability: "session" },
  { name: "repoContextCacheTtlMs", type: "int", default: 60000, min: 1000, max: 3600000, capability: "session" },

  { name: "takeoverEnabled", type: "bool", default: true, env: "OPENVIKING_TAKEOVER", capability: "session" },
  { name: "takeoverTokenThreshold", type: "int", default: 30000, min: 1, max: 1000000, capability: "session" },
  { name: "takeoverKeepRecentTurns", type: "int", default: 3, min: 0, max: 100, capability: "session" },
  { name: "takeoverOverviewBudget", type: "int", default: 3000, min: 100, max: 50000, capability: "session" },
  { name: "takeoverOverviewPollMs", type: "int", default: 2000, min: 0, max: 60000, capability: "session" },
  { name: "takeoverOverviewPollMax", type: "int", default: 15, min: 1, max: 120, capability: "session" },

  { name: "bypassSession", type: "bool", default: false, env: "OPENVIKING_BYPASS_SESSION", capability: "session" },
  {
    name: "bypassSessionPatterns",
    type: "list",
    default: [],
    env: "OPENVIKING_BYPASS_SESSION_PATTERNS",
    // pi shipped this under its own name before the shared matcher existed.
    aliases: ["bypassPatterns"],
    workspace: "bypass.session_patterns",
    capability: "session",
  },

  // ── debug ─────────────────────────────────────────────────────────────
  { name: "debug", type: "bool", default: false, env: "OPENVIKING_DEBUG", capability: "debug" },
  // The default path is per-harness and built from a log directory the loader
  // owns, so the schema only carries what a user set.
  { name: "debugLogPath", type: "string", default: "", env: "OPENVIKING_DEBUG_LOG", capability: "debug" },
  { name: "logLevel", type: "enum", values: ["silent", "error", "info"], default: "error", env: "OPENVIKING_LOG_LEVEL", capability: "debug" },
];

export const KNOB_BY_NAME = new Map(KNOBS.map((knob) => [knob.name, knob]));

/** Every accepted spelling → the canonical knob it means. */
export const KNOB_BY_KEY = new Map();
for (const knob of KNOBS) {
  KNOB_BY_KEY.set(knob.name, knob);
  for (const alias of knob.aliases || []) KNOB_BY_KEY.set(alias, knob);
}

/** Every key a user may legitimately write in `ovcli.conf`'s plugin section. */
export function pluginConfigKeys() {
  return new Set(KNOB_BY_KEY.keys());
}

/** The workspace file's dotted keys → the knob each one sets. */
export const WORKSPACE_KNOB_MAP = Object.fromEntries(
  KNOBS.filter((knob) => knob.workspace).map((knob) => [knob.workspace, knob.name]),
);

/** The enum members and clamps the workspace validator reports on. */
export const WORKSPACE_ENUMS = Object.fromEntries(
  KNOBS.filter((knob) => knob.workspace).map((knob) => [knob.workspace, knob.values || null]),
);

export const WORKSPACE_RANGES = Object.fromEntries(
  KNOBS.filter((knob) => knob.workspace && knob.min !== undefined && knob.max !== undefined)
    .map((knob) => [knob.workspace, {
      min: knob.min,
      max: knob.max,
      integer: knob.type === "int",
    }]),
);

/**
 * The harnesses that may appear as a per-harness object inside `plugin`.
 *
 * The key is snake_case because that is what ov.conf's own sections use; the
 * hyphenated spelling a host calls itself (`claude-code`, `trae-cn`) is
 * accepted too, so a user who copies the harness name out of the installer
 * lands on the right override instead of on a silent no-op.
 */
export const HARNESS_KEYS = {
  claudeCode: "claude_code",
  codex: "codex",
  cursor: "cursor",
  trae: "trae",
  traeCn: "trae_cn",
  zcode: "zcode",
  opencode: "opencode",
  dsh: "dsh",
  pi: "pi",
  openclaw: "openclaw",
};

export function harnessKey(harness) {
  return String(harness || "").trim().toLowerCase().replace(/-/g, "_");
}

/** Both accepted spellings of every harness, for the `plugin` section. */
export const HARNESS_CONFIG_KEYS = new Set(
  Object.values(HARNESS_KEYS).flatMap((key) => [key, key.replace(/_/g, "-")]),
);

export function knobDefault(knob, harness = "") {
  if (harness && knob.harness && knob.harness[harness] !== undefined) return knob.harness[harness];
  return Array.isArray(knob.default) ? [...knob.default] : knob.default;
}

const TRUE_WORDS = ["1", "true", "yes", "on"];
const FALSE_WORDS = ["0", "false", "no", "off"];

/**
 * Coerce one raw value to the knob's type.
 *
 * A value out of range is clamped and a value of the wrong shape falls back to
 * the default: a hook that dies because someone typed a letter into a number
 * would take the host's session down with it.
 */
export function coerceKnobValue(knob, raw, fallback) {
  if (raw === undefined || raw === null) return fallback;
  switch (knob.type) {
    case "bool": {
      if (typeof raw === "boolean") return raw;
      const word = String(raw).trim().toLowerCase();
      if (TRUE_WORDS.includes(word)) return true;
      if (FALSE_WORDS.includes(word)) return false;
      return fallback;
    }
    case "int":
    case "number": {
      if (typeof raw === "string" && !raw.trim()) return fallback;
      const value = knob.type === "int" ? Math.round(Number(raw)) : Number(raw);
      if (!Number.isFinite(value)) return fallback;
      const floored = knob.min === undefined ? value : Math.max(knob.min, value);
      return knob.max === undefined ? floored : Math.min(knob.max, floored);
    }
    case "enum": {
      const word = String(raw ?? "").trim();
      return (knob.values || []).includes(word) ? word : fallback;
    }
    // A peer template may be one string or an ordered list of fallbacks, and
    // `String([a, b])` would quietly turn the list into one bogus template.
    case "template": {
      if (Array.isArray(raw)) {
        const items = raw.filter((item) => typeof item === "string").map((item) => item.trim()).filter(Boolean);
        return items.length ? items : fallback;
      }
      const text = String(raw ?? "").trim();
      return text ? text : fallback;
    }
    case "list": {
      const items = Array.isArray(raw)
        ? raw
        : typeof raw === "string" ? raw.split(",") : null;
      if (!items) return fallback;
      return items.filter((item) => typeof item === "string").map((item) => item.trim()).filter(Boolean);
    }
    default: {
      const text = String(raw ?? "").trim();
      return text ? text : fallback;
    }
  }
}

/**
 * Resolve every knob through the layers, lowest priority first.
 *
 * `layers` is `[{ name, data }]`; `env` is the top layer and needs no entry.
 * Returns the settings plus the names a layer actually supplied, which is what
 * the `*Configured` flags report — several call sites send a field to the
 * server only when the user asked for it, and a default must not look asked-for.
 */
export function resolveKnobs({ harness = "", layers = [], env = {} } = {}) {
  const settings = {};
  const configured = new Set();
  const sources = {};
  // A value that would not parse is not a configuration: without this sentinel
  // `recallLimit: "abc"` would report as configured and send a default to the
  // server dressed up as the user's choice.
  const UNPARSEABLE = Symbol("unparseable");

  const take = (knob, raw, source, current) => {
    const value = coerceKnobValue(knob, raw, UNPARSEABLE);
    if (value === UNPARSEABLE) return current;
    configured.add(knob.name);
    sources[knob.name] = source;
    return value;
  };

  for (const knob of KNOBS) {
    let value = knobDefault(knob, harness);
    for (const layer of layers) {
      const data = layer?.data;
      if (!data || typeof data !== "object") continue;
      // Aliases first so the canonical name wins when one layer carries both:
      // a file with `recallCompressThinking` beside its older
      // `recallCompressReasoningEffort` means the newer one.
      for (const key of [...(knob.aliases || []), knob.name]) {
        if (!Object.prototype.hasOwnProperty.call(data, key)) continue;
        if (data[key] === undefined) continue;
        value = take(knob, data[key], layer.name || "file", value);
      }
    }
    if (knob.env) {
      const raw = env[knob.env];
      if (raw !== undefined && raw !== null && String(raw) !== "") {
        value = take(knob, raw, "env", value);
      }
    }
    settings[knob.name] = value;
  }

  return { settings, configured, sources };
}
