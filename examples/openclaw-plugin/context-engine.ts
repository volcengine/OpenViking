import type { OpenVikingClient } from "./client.js";
import type { MemoryOpenVikingConfig } from "./config.js";
import {
  getCaptureDecision,
  extractNewTurnTexts,
} from "./text-utils.js";
import {
  trimForLog,
  toJsonLog,
  summarizeExtractedMemories,
} from "./memory-ranking.js";

type AgentMessage = {
  role?: string;
  content?: unknown;
};

type ContextEngineInfo = {
  id: string;
  name: string;
  version?: string;
};

type AssembleResult = {
  messages: AgentMessage[];
  estimatedTokens: number;
  systemPromptAddition?: string;
};

type IngestResult = {
  ingested: boolean;
};

type IngestBatchResult = {
  ingestedCount: number;
};

type CompactResult = {
  ok: boolean;
  compacted: boolean;
  reason?: string;
  result?: unknown;
};

type ContextEngine = {
  info: ContextEngineInfo;
  ingest: (params: { sessionId: string; message: AgentMessage; isHeartbeat?: boolean }) => Promise<IngestResult>;
  ingestBatch?: (params: {
    sessionId: string;
    messages: AgentMessage[];
    isHeartbeat?: boolean;
  }) => Promise<IngestBatchResult>;
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
  assemble: (params: { sessionId: string; messages: AgentMessage[]; tokenBudget?: number }) => Promise<AssembleResult>;
  compact: (params: {
    sessionId: string;
    sessionFile: string;
    tokenBudget?: number;
    force?: boolean;
    currentTokenCount?: number;
    compactionTarget?: "budget" | "threshold";
    customInstructions?: string;
    runtimeContext?: Record<string, unknown>;
  }) => Promise<CompactResult>;
};

type Logger = {
  info: (msg: string) => void;
  warn?: (msg: string) => void;
  error: (msg: string) => void;
};

function estimateTokens(messages: AgentMessage[]): number {
  return Math.max(1, messages.length * 80);
}

async function tryLegacyCompact(params: {
  sessionId: string;
  sessionFile: string;
  tokenBudget?: number;
  force?: boolean;
  currentTokenCount?: number;
  compactionTarget?: "budget" | "threshold";
  customInstructions?: string;
  runtimeContext?: Record<string, unknown>;
}): Promise<CompactResult | null> {
  const candidates = [
    "openclaw/context-engine/legacy",
    "openclaw/dist/context-engine/legacy.js",
  ];

  for (const path of candidates) {
    try {
      const mod = (await import(path)) as {
        LegacyContextEngine?: new () => {
          compact: (arg: typeof params) => Promise<CompactResult>;
        };
      };
      if (!mod?.LegacyContextEngine) {
        continue;
      }
      const legacy = new mod.LegacyContextEngine();
      return legacy.compact(params);
    } catch {
      // continue
    }
  }

  return null;
}

function warnOrInfo(logger: Logger, message: string): void {
  if (typeof logger.warn === "function") {
    logger.warn(message);
    return;
  }
  logger.info(message);
}

export function createMemoryOpenVikingContextEngine(params: {
  id: string;
  name: string;
  version?: string;
  cfg: Required<MemoryOpenVikingConfig>;
  logger: Logger;
  getClient: () => Promise<OpenVikingClient>;
  resolveAgentId: (sessionId: string) => string;
}): ContextEngine {
  const {
    id,
    name,
    version,
    cfg,
    logger,
    getClient,
    resolveAgentId,
  } = params;

  const switchClientAgent = async (sessionId: string, phase: "assemble" | "afterTurn") => {
    const client = await getClient();
    const resolvedAgentId = resolveAgentId(sessionId);
    const before = client.getAgentId();
    if (resolvedAgentId && resolvedAgentId !== before) {
      client.setAgentId(resolvedAgentId);
      logger.info(`openviking: switched to agentId=${resolvedAgentId} for ${phase}`);
    }
    return client;
  };

  return {
    info: {
      id,
      name,
      version,
    },

    async ingest(): Promise<IngestResult> {
      // Keep canonical capture behavior in afterTurn (same semantics as old agent_end hook).
      return { ingested: false };
    },

    async ingestBatch(): Promise<IngestBatchResult> {
      // Keep canonical capture behavior in afterTurn (same semantics as old agent_end hook).
      return { ingestedCount: 0 };
    },

    async assemble(assembleParams): Promise<AssembleResult> {
      return {
        messages: assembleParams.messages,
        estimatedTokens: estimateTokens(assembleParams.messages),
      };
    },

    async afterTurn(afterTurnParams): Promise<void> {
      if (!cfg.autoCapture) {
        return;
      }

      try {
        await switchClientAgent(afterTurnParams.sessionId, "afterTurn");

        const messages = afterTurnParams.messages ?? [];
        if (messages.length === 0) {
          logger.info("openviking: auto-capture skipped (messages=0)");
          return;
        }

        const start =
          typeof afterTurnParams.prePromptMessageCount === "number" &&
          afterTurnParams.prePromptMessageCount >= 0
            ? afterTurnParams.prePromptMessageCount
            : 0;

        const { texts: newTexts, newCount } = extractNewTurnTexts(messages, start);

        if (newTexts.length === 0) {
          logger.info("openviking: auto-capture skipped (no new user/assistant messages)");
          return;
        }

        const turnText = newTexts.join("\n");
        const decision = getCaptureDecision(turnText, cfg.captureMode, cfg.captureMaxLength);
        const preview = turnText.length > 80 ? `${turnText.slice(0, 80)}...` : turnText;
        logger.info(
          "openviking: capture-check " +
            `shouldCapture=${String(decision.shouldCapture)} ` +
            `reason=${decision.reason} newMsgCount=${newCount} text=\"${preview}\"`,
        );

        if (!decision.shouldCapture) {
          logger.info("openviking: auto-capture skipped (capture decision rejected)");
          return;
        }

        const client = await getClient();
        const sessionId = await client.createSession();
        try {
          await client.addSessionMessage(sessionId, "user", decision.normalizedText);
          await client.getSession(sessionId).catch(() => ({}));
          const extracted = await client.extractSessionMemories(sessionId);

          logger.info(
            `openviking: auto-captured ${newCount} new messages, extracted ${extracted.length} memories`,
          );
          logger.info(
            `openviking: capture-detail ${toJsonLog({
              capturedCount: newCount,
              captured: [trimForLog(turnText, 260)],
              extractedCount: extracted.length,
              extracted: summarizeExtractedMemories(extracted),
            })}`,
          );
          if (extracted.length === 0) {
            warnOrInfo(
              logger,
              "openviking: auto-capture completed but extract returned 0 memories. " +
                "Check OpenViking server logs for embedding/extract errors.",
            );
          }
        } finally {
          await client.deleteSession(sessionId).catch(() => {});
        }
      } catch (err) {
        warnOrInfo(logger, `openviking: auto-capture failed: ${String(err)}`);
      }
    },

    async compact(compactParams): Promise<CompactResult> {
      const delegated = await tryLegacyCompact(compactParams);
      if (delegated) {
        return delegated;
      }

      warnOrInfo(
        logger,
        "openviking: legacy compaction delegation unavailable; skipping compact",
      );

      return {
        ok: true,
        compacted: false,
        reason: "legacy_compact_unavailable",
      };
    },
  };
}
