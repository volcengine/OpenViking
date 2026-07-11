import type { MemoryOpenVikingConfig } from "../config.js";
import { RuntimeQueryConfigStore } from "../query-config.js";
import { RecallTraceRecorder } from "../recall-trace.js";

type Logger = {
  warn?: (message: string) => void;
};

type RuntimeStateConfig = Required<
  Pick<
    MemoryOpenVikingConfig,
    | "runtimeQueryConfigPath"
    | "traceRecall"
    | "traceRecallMaxEntries"
    | "traceRecallPersist"
    | "traceRecallDir"
    | "traceRecallIncludeRawUserPreview"
    | "traceRecallRetentionDays"
    | "traceRecallQueryMaxDays"
  >
>;

export function createOpenVikingRuntimeState(options: {
  cfg: Required<MemoryOpenVikingConfig> & RuntimeStateConfig;
  logger: Logger;
}) {
  const { cfg, logger } = options;

  const queryConfigStore = new RuntimeQueryConfigStore({
    staticConfig: cfg,
    path: cfg.runtimeQueryConfigPath || undefined,
  });
  void queryConfigStore.load().catch((err) => {
    logger.warn?.(`openviking: failed to load runtime query config: ${String(err)}`);
  });

  const traceRecorder = cfg.traceRecall
    ? new RecallTraceRecorder({
        memoryMaxEntries: cfg.traceRecallMaxEntries,
        persist: cfg.traceRecallPersist,
        traceDir: cfg.traceRecallDir,
        includeRawUserPreview: cfg.traceRecallIncludeRawUserPreview,
        retentionDays: cfg.traceRecallRetentionDays,
        queryMaxDays: cfg.traceRecallQueryMaxDays,
      })
    : undefined;

  return {
    queryConfigStore,
    traceRecorder,
  };
}
