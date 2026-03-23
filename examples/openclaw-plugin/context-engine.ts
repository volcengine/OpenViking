import type { OpenVikingClient, CommitSessionResult } from "./client.js";
import type { MemoryOpenVikingConfig } from "./config.js";
import {
  getCaptureDecision,
  extractNewTurnTexts,
} from "./text-utils.js";
import {
  trimForLog,
  toJsonLog,
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

export type ContextEngineWithSessionMapping = ContextEngine & {
  /** Return the OV session ID for an OpenClaw sessionKey (identity: sessionKey IS the OV session ID). */
  getOVSessionForKey: (sessionKey: string) => string;
  /** Ensure an OV session exists on the server for the given OpenClaw sessionKey (auto-created by getSession if absent). */
  resolveOVSession: (sessionKey: string) => Promise<string>;
  /** Commit (archive + extract) the OV session. Returns true on success. */
  commitOVSession: (sessionKey: string) => Promise<boolean>;
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

/** Sum all category counts in a memories_extracted record. */
function totalMemories(m: CommitSessionResult["memories_extracted"]): number {
  if (!m || typeof m !== "object") return 0;
  return Object.values(m).reduce((sum, n) => sum + (n ?? 0), 0);
}

export function createMemoryOpenVikingContextEngine(params: {
  id: string;
  name: string;
  version?: string;
  cfg: Required<MemoryOpenVikingConfig>;
  logger: Logger;
  getClient: () => Promise<OpenVikingClient>;
  resolveAgentId: (sessionId: string) => string;
}): ContextEngineWithSessionMapping {
  const {
    id,
    name,
    version,
    cfg,
    logger,
    getClient,
    resolveAgentId,
  } = params;

  /** Returns true when commit + extraction succeeded, false otherwise. */
  async function doCommitOVSession(sessionKey: string): Promise<boolean> {
    try {
      const client = await getClient();
      const agentId = resolveAgentId(sessionKey);
      const commitResult = await client.commitSession(sessionKey, { wait: true, agentId });
      const memCount = totalMemories(commitResult.memories_extracted);
      if (commitResult.status === "failed") {
        warnOrInfo(logger, `openviking: commit Phase 2 failed for sessionKey=${sessionKey}: ${commitResult.error ?? "unknown"}`);
        return false;
      }
      if (commitResult.status === "timeout") {
        warnOrInfo(logger, `openviking: commit Phase 2 timed out for sessionKey=${sessionKey}, task_id=${commitResult.task_id ?? "none"}`);
        return false;
      }
      logger.info(
        `openviking: committed OV session for sessionKey=${sessionKey}, archived=${commitResult.archived ?? false}, memories=${memCount}, task_id=${commitResult.task_id ?? "none"}`,
      );
      return true;
    } catch (err) {
      warnOrInfo(logger, `openviking: commit failed for sessionKey=${sessionKey}: ${String(err)}`);
      return false;
    }
  }

  function extractSessionKey(runtimeContext: Record<string, unknown> | undefined): string | undefined {
    if (!runtimeContext) {
      return undefined;
    }
    const key = runtimeContext.sessionKey;
    return typeof key === "string" && key.trim() ? key.trim() : undefined;
  }

  return {
    info: {
      id,
      name,
      version,
    },

    // --- session-mapping extensions ---

    getOVSessionForKey: (sessionKey: string) => sessionKey,

    async resolveOVSession(sessionKey: string): Promise<string> {
      return sessionKey;
    },

    commitOVSession: doCommitOVSession,

    // --- standard ContextEngine methods ---

    async ingest(): Promise<IngestResult> {
      return { ingested: false };
    },

    async ingestBatch(): Promise<IngestBatchResult> {
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
        const sessionKey = extractSessionKey(afterTurnParams.runtimeContext);
        const agentId = resolveAgentId(sessionKey ?? afterTurnParams.sessionId);

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
        const OVSessionId = sessionKey ?? afterTurnParams.sessionId;
        await client.addSessionMessage(OVSessionId, "user", decision.normalizedText, agentId);

        const session = await client.getSession(OVSessionId, agentId);
        const pendingTokens = session.pending_tokens ?? 0;

        if (pendingTokens < cfg.commitTokenThreshold) {
          logger.info(
            `openviking: pending_tokens=${pendingTokens}/${cfg.commitTokenThreshold} in session=${OVSessionId}, deferring commit`,
          );
          return;
        }

        const commitResult = await client.commitSession(OVSessionId, { wait: true, agentId });
        const memCount = totalMemories(commitResult.memories_extracted);
        logger.info(
          `openviking: committed ${newCount} messages in session=${OVSessionId}, ` +
            `status=${commitResult.status}, archived=${commitResult.archived ?? false}, memories=${memCount}, ` +
            `task_id=${commitResult.task_id ?? "none"} ${toJsonLog({ captured: [trimForLog(turnText, 260)] })}`,
        );
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
