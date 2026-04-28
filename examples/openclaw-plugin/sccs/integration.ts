import { compressToolMessages } from "./compressor.js";
import { DiskBackedStore } from "./storage.js";
import { estimateTokensForMessages, resolveHomePath } from "./utils.js";
import { createFetchOriginalDataTool } from "./ref-tool.js";

export type SccsConfig = {
  enabled: boolean;
  compressThreshold: number;
  summaryMaxChars: number;
  enableSmartSummary: boolean;
  storageTtlSeconds: number;
  storageDir: string;
  maxEntries?: number;
};

type AgentMessage = { role?: string; content?: unknown };

type AssembleResult = {
  messages: AgentMessage[];
  estimatedTokens: number;
  systemPromptAddition?: string;
};

type ContextEngine = {
  info: { id: string; name: string; version?: string };
  assemble: (params: { sessionId: string; messages: AgentMessage[]; tokenBudget?: number }) => Promise<AssembleResult>;
  ingest: (params: { sessionId: string; message: AgentMessage; isHeartbeat?: boolean }) => Promise<{ ingested: boolean }>;
  ingestBatch?: (params: { sessionId: string; messages: AgentMessage[]; isHeartbeat?: boolean }) => Promise<{ ingestedCount: number }>;
  afterTurn?: (params: {
    sessionId: string;
    sessionFile: string;
    messages: AgentMessage[];
    prePromptMessageCount: number;
    autoCompactionSummary?: string;
    isHeartbeat?: boolean;
    tokenBudget?: number;
    runtimeContext?: Record<string, unknown>;
  }) => Promise<void>;
  compact: (params: {
    sessionId: string;
    sessionFile: string;
    tokenBudget?: number;
    force?: boolean;
    currentTokenCount?: number;
    compactionTarget?: "budget" | "threshold";
    customInstructions?: string;
    runtimeContext?: Record<string, unknown>;
  }) => Promise<{ ok: boolean; compacted: boolean; reason?: string; result?: unknown }>;
};

type Logger = { info: (msg: string) => void; warn?: (msg: string) => void };

export function createSccsIntegration(params: { cfg: SccsConfig; logger: Logger }) {
  if (!params.cfg.enabled) {
    return {
      enabled: false as const,
      wrapContextEngine: <T extends ContextEngine>(engine: T): T => engine,
      tool: undefined,
    };
  }

  const store = new DiskBackedStore({
    dir: resolveHomePath(params.cfg.storageDir),
    maxEntries: params.cfg.maxEntries,
  });

  const wrapContextEngine = <T extends ContextEngine>(engine: T): T => {
    return {
      ...engine,
      assemble: async (assembleParams) => {
        const base = await engine.assemble(assembleParams);
        const compressed = await compressToolMessages({
          messages: base.messages,
          config: {
            compressThreshold: params.cfg.compressThreshold,
            summaryMaxChars: params.cfg.summaryMaxChars,
            enableSmartSummary: params.cfg.enableSmartSummary,
            storageTtlSeconds: params.cfg.storageTtlSeconds,
          },
          store,
          logger: params.logger,
        });

        const systemPromptAddition =
          base.systemPromptAddition && compressed.systemPromptAddition
            ? `${base.systemPromptAddition}\n\n${compressed.systemPromptAddition}`
            : base.systemPromptAddition || compressed.systemPromptAddition;

        return {
          ...base,
          messages: compressed.messages,
          estimatedTokens: estimateTokensForMessages(compressed.messages),
          ...(systemPromptAddition ? { systemPromptAddition } : {}),
        };
      },
    };
  };

  const tool = createFetchOriginalDataTool({
    store,
    logger: params.logger,
  });

  return {
    enabled: true as const,
    wrapContextEngine,
    tool,
  };
}
