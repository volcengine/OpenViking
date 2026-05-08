export const PACKAGE_NAME = "@openviking/copilot-shared" as const;

export {
  isPluginEnabled,
  loadConfig,
  type AgentIdDefault,
  type CaptureMode,
  type LoadConfigOptions,
  type PluginConfig,
} from "./config.js";

export {
  createDebugLogger,
  DEFAULT_MAX_BYTES as DEBUG_LOG_DEFAULT_MAX_BYTES,
  type CreateDebugLoggerOptions,
  type DebugLogger,
} from "./debug/logger.js";

export {
  runWriteTask,
  spawnDetached,
  type DetachedSpawnOptions,
  type RunWriteTaskOptions,
  type SpawnDetachedResult,
} from "./util/async-writer.js";

export {
  deriveSessionId,
  SESSION_ID_PREFIX,
} from "./session/id.js";

export {
  OVClient,
  type CommitOptions,
  type OVClientBypassContext,
  type OVClientOptions,
  type OVResult,
  type OVTurn,
  type RecallHit,
  type RecallOptions,
} from "./ov-client.js";

export {
  INJECTED_BLOCK_PATTERNS,
  sanitize,
  stripInjectedBlocks,
} from "./capture/sanitize.js";

export {
  buildQueryProfile,
  clampScore,
  dedupeItems,
  estimateTokens,
  isEventOrCaseItem,
  lexicalOverlapBoost,
  rankItem,
  rankRecallHits,
  type QueryProfile,
  type RankRecallOptions,
} from "./recall/rank.js";
