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
  runDebugRecall,
  type DebugRecallArgs,
  type DebugRecallDeps,
  type DebugRecallResult,
  type RecallDebuggerClient,
} from "./debug/recall-debugger.js";

export {
  runDebugCapture,
  type DebugCaptureArgs,
  type DebugCaptureDeps,
  type DebugCaptureResult,
} from "./debug/capture-debugger.js";

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
  type ReadOptions,
  type RecallHit,
  type RecallOptions,
} from "./ov-client.js";

export {
  INJECTED_BLOCK_PATTERNS,
  sanitize,
  stripInjectedBlocks,
} from "./capture/sanitize.js";

export {
  canonicaliseTranscript,
  fromCaptureToolArgs,
  fromVSCodeChatHistory,
  type CanonicalTurnInput,
  type CaptureToolArgs,
  type TranscriptOptions,
  type VSCodeChatRequestTurnLike,
  type VSCodeChatResponseTurnLike,
  type VSCodeChatTurnLike,
} from "./capture/transcript.js";

export {
  CommitQueue,
  type CommitClient,
  type CommitQueueOptions,
  type EnqueueResult,
} from "./capture/commit-queue.js";

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

export {
  buildRecallContextBlock,
  type BuildRecallContextOptions,
  type BuildRecallContextResult,
  type RecallContextClient,
  type RecallContextConfig,
  type RecallContextState,
} from "./recall/context.js";

export {
  formatRecallBlock,
  type FormatRecallBlockOptions,
  type FormatRecallBlockResult,
} from "./recall/format.js";

export {
  OPENVIKING_RECALL_TOOL_DESCRIPTION,
  OPENVIKING_RECALL_TOOL_DISPLAY_NAME,
  OPENVIKING_RECALL_TOOL_NAME,
  OPENVIKING_RECALL_TOOL_REFERENCE_NAME,
  OPENVIKING_RECALL_TOOL_USER_DESCRIPTION,
} from "./recall/tool-description.js";

export {
  DEFAULT_MAX_ENTRIES as RECALL_CACHE_DEFAULT_MAX_ENTRIES,
  DEFAULT_TTL_MS as RECALL_CACHE_DEFAULT_TTL_MS,
  RecallCache,
  type RecallCacheKey,
  type RecallCacheOptions,
} from "./recall/cache.js";
