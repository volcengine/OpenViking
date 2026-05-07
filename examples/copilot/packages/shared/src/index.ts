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
