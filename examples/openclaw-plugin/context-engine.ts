import type { OpenVikingClient } from "./client.js";
import type { ParsedMemoryOpenVikingConfig } from "./config.js";
import type { RuntimeQueryConfigStore } from "./query-config.js";
import {
  AUTO_RECALL_SOURCE_MARKER,
} from "./auto-recall.js";
import {
  compileSessionPatterns,
  getCaptureDecision,
  shouldBypassSession,
} from "./text-utils.js";
import type { RecallTraceEntry } from "./recall-trace.js";
import { estimateAgentMessageTokens, estimateAgentMessagesTokens } from "./token-estimator.js";
import { openClawSessionToOvStorageId } from "./routing/identity-routing.js";
import type { AgentMessage } from "./services/context-message-adapter.js";
import {
  assembleOpenVikingSession,
  afterTurnOpenVikingSession,
  compactOpenVikingSession,
  commitOpenVikingSession,
} from "./services/context-lifecycle-service.js";
import { loadRuntimeCompactionDelegate } from "./plugin/openclaw-runtime-compaction.js";

type ContextEngineInfo = {
  id: string;
  name: string;
  version?: string;
  ownsCompaction: true;
  /** OpenClaw >=2026.8.1: without both declarations (and commitTurn) the engine is degraded to "legacy" every turn. */
  transcriptSemantics: {
    currentTurnFence: "before-current-turn-entry-v1";
    turnAdvancementIdempotency: "atomic-idempotent-v1";
  };
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
  result?: {
    summary?: string;
    firstKeptEntryId?: string;
    tokensBefore: number;
    tokensAfter?: number;
    details?: unknown;
  };
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
    sessionKey?: string;
  }) => Promise<void>;
  assemble: (params: {
    sessionId: string;
    sessionKey?: string;
    messages: AgentMessage[];
    prompt?: string;
    tokenBudget?: number;
    runtimeContext?: Record<string, unknown>;
  }) => Promise<AssembleResult>;
  /** OpenClaw >=2026.8.1 durable turn advancement; retried with the same advancementKey after host failure. */
  commitTurn: (params: {
    advancementKey: string;
    messages: AgentMessage[];
    sessionId: string;
    sessionKey?: string;
    isHeartbeat?: boolean;
  }) => Promise<{ status: "committed" | "duplicate" }>;
  compact: (params: {
    sessionId: string;
    sessionKey?: string;
    sessionFile: string;
    tokenBudget?: number;
    force?: boolean;
    currentTokenCount?: number;
    compactionTarget?: "budget" | "threshold";
    customInstructions?: string;
    runtimeContext?: Record<string, unknown>;
  }) => Promise<CompactResult>;
};

export type ContextEngineWithCommit = ContextEngine & {
  /** Commit (archive + extract) the OV session. Returns true on success. */
  commitOVSession: (params: {
    sessionId: string;
    sessionKey?: string;
    runtimeContext?: Record<string, unknown>;
  }) => Promise<boolean>;
};

type Logger = {
  info: (msg: string) => void;
  warn?: (msg: string) => void;
  error: (msg: string) => void;
};

function roughEstimate(messages: AgentMessage[]): number {
  return estimateAgentMessagesTokens(messages);
}

function msgTokenEstimate(msg: AgentMessage): number {
  return estimateAgentMessageTokens(msg);
}

function messageDigest(messages: AgentMessage[], maxCharsPerMsg = 2000): Array<{role: string; content: string; tokens: number; truncated: boolean}> {
  return messages.map((msg) => {
    const m = msg as Record<string, unknown>;
    const role = String(m.role ?? "unknown");
    const raw = m.content;
    let text: string;
    if (typeof raw === "string") {
      text = raw;
    } else if (Array.isArray(raw)) {
      text = (raw as Record<string, unknown>[])
        .map((b) => {
          if (b.type === "text") return String(b.text ?? "");
          if (b.type === "toolCall") return `[toolCall: ${String(b.name)}(${JSON.stringify(b.arguments ?? {}).slice(0, 200)})]`;
          if (b.type === "toolResult") return `[toolResult: ${JSON.stringify(b.content ?? "").slice(0, 200)}]`;
          return `[${String(b.type)}]`;
        })
        .join("\n");
    } else {
      text = JSON.stringify(raw) ?? "";
    }
    const truncated = text.length > maxCharsPerMsg;
    return {
      role,
      content: truncated ? text.slice(0, maxCharsPerMsg) + "..." : text,
      tokens: msgTokenEstimate(msg),
      truncated,
    };
  });
}

function extractAgentMessageText(message: AgentMessage | undefined): string {
  if (!message) {
    return "";
  }
  const raw = message.content;
  if (typeof raw === "string") {
    return raw;
  }
  if (Array.isArray(raw)) {
    return raw
      .map((block) => {
        if (!block || typeof block !== "object") {
          return "";
        }
        const b = block as Record<string, unknown>;
        if (b.type === "text" && typeof b.text === "string") {
          return b.text;
        }
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }
  return "";
}

function hasAutoRecallBlock(message: AgentMessage | undefined): boolean {
  return extractAgentMessageText(message).includes(AUTO_RECALL_SOURCE_MARKER);
}

function prependTextToMessageContent(content: unknown, text: string): unknown {
  if (typeof content === "string") {
    return `${text}\n\n${content}`;
  }
  if (Array.isArray(content)) {
    if (content.length === 0) {
      return [{ type: "text", text }];
    }
    const first = content[0];
    if (
      first &&
      typeof first === "object" &&
      (first as Record<string, unknown>).type === "text" &&
      typeof (first as Record<string, unknown>).text === "string"
    ) {
      return [
        {
          ...(first as Record<string, unknown>),
          text: `${text}\n\n${(first as Record<string, unknown>).text as string}`,
        },
        ...content.slice(1),
      ];
    }
    return [{ type: "text", text }, ...content];
  }
  return text;
}

function prependRecallToLatestUserMessage(messages: AgentMessage[], recallBlock: string): AgentMessage[] {
  const latest = messages.at(-1);
  if (!latest || latest.role !== "user" || hasAutoRecallBlock(latest)) {
    return messages;
  }
  return [
    ...messages.slice(0, -1),
    {
      ...latest,
      content: prependTextToMessageContent(latest.content, recallBlock),
    },
  ];
}

function emitDiag(log: Logger, stage: string, sessionId: string, data: Record<string, unknown>, enabled = true): void {
  if (!enabled) return;
  log.info(`openviking: diag ${JSON.stringify({ ts: Date.now(), stage, sessionId, data })}`);
}

function validTokenBudget(raw: unknown): number | undefined {
  if (typeof raw === "number" && Number.isFinite(raw) && raw > 0) {
    return raw;
  }
  return undefined;
}

// OpenClaw 9.1/9.2 still capture via afterTurn even when commitTurn exists.
// 9.3 (#140024) defers admitted turns to commitTurn; standalone runners keep
// afterTurn. The registration API's runtime.version is the HOST version.
function usesDeferredTurnCapture(version: string | undefined): boolean | undefined {
  const match = version?.trim().match(
    /^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/,
  );
  if (!match) return undefined;
  const parts = match.slice(1, 4).map(Number);
  if (!parts.every(Number.isSafeInteger)) return undefined;
  const [year, month, day] = parts;
  // OpenClaw uses 0.0.0 when version metadata cannot be resolved. More generally,
  // versions outside our supported range cannot prove who owns capture.
  if (year < 2026 || (year === 2026 && (month < 5 || (month === 5 && day < 27)))) {
    return undefined;
  }
  const boundary = year === 2026 && month === 9 && day === 3;
  // The 9.3 prerelease train can straddle the host change. Numeric packaging
  // revisions (-1, -2, ...) refer to the stable release; do not guess for betas.
  if (boundary && match[4] && !/^\d+$/.test(match[4])) return undefined;
  return year > 2026 || (year === 2026 && (month > 9 || (month === 9 && day >= 3)));
}

export function createMemoryOpenVikingContextEngine(params: {
  id: string;
  name: string;
  version?: string;
  hostVersion?: string;
  cfg: ParsedMemoryOpenVikingConfig;
  logger: Logger;
  getClient: () => Promise<OpenVikingClient>;
  /** Extra args help match hook-populated routing when OpenClaw provides sessionKey / OV session id. */
  resolveAgentId: (sessionId: string, sessionKey?: string, ovSessionId?: string) => string;
  rememberSessionAgentId?: (ctx: {
    agentId?: string;
    sessionId?: string;
    sessionKey?: string;
    ovSessionId?: string;
  }) => void;
  queryConfigStore?: RuntimeQueryConfigStore;
  traceRecorder?: { record(entry: RecallTraceEntry): void; recordAndFlush?: (entry: RecallTraceEntry) => Promise<unknown> };
}): ContextEngineWithCommit {
  const {
    id,
    name,
    version,
    cfg,
    logger,
    getClient,
    resolveAgentId,
    rememberSessionAgentId,
    queryConfigStore,
    traceRecorder,
  } = params;

  const diagEnabled = cfg.emitStandardDiagnostics;
  const bypassSessionPatterns = compileSessionPatterns(cfg.bypassSessionPatterns);
  const diag = (stage: string, sessionId: string, data: Record<string, unknown>) =>
    emitDiag(logger, stage, sessionId, data, diagEnabled);

  const isBypassedSession = (params: { sessionId?: string; sessionKey?: string }): boolean =>
    shouldBypassSession(params, bypassSessionPatterns);

  async function doCommitOVSession(params: {
    sessionId: string;
    sessionKey?: string;
    runtimeContext?: Record<string, unknown>;
  }): Promise<boolean> {
    const { sessionId } = params;
    const { sessionKey } = resolveSessionIdentity(params);
    return commitOpenVikingSession({
      sessionId,
      sessionKey,
      getClient,
      logger,
      rememberSessionAgentId,
      isBypassedSession,
    });
  }

  function extractSessionKey(runtimeContext: Record<string, unknown> | undefined): string | undefined {
    if (!runtimeContext) {
      return undefined;
    }
    const key = runtimeContext.sessionKey;
    return typeof key === "string" && key.trim() ? key.trim() : undefined;
  }

  function resolveSessionKey(params: {
    sessionKey?: string;
    runtimeContext?: Record<string, unknown>;
  }): string | undefined {
    const direct = typeof params.sessionKey === "string" ? params.sessionKey.trim() : "";
    if (direct) {
      return direct;
    }
    return extractSessionKey(params.runtimeContext);
  }

  function resolveSessionIdentity(params: {
    sessionId: string;
    sessionKey?: string;
    runtimeContext?: Record<string, unknown>;
  }): { sessionKey: string | undefined; ovSessionId: string } {
    const sessionKey = resolveSessionKey(params);
    return {
      sessionKey,
      ovSessionId: openClawSessionToOvStorageId(params.sessionId, sessionKey),
    };
  }

  const committedTurnKeys = new Set<string>();
  const inFlightTurns = new Map<string, Promise<void>>();
  const deferredTurnCapture = usesDeferredTurnCapture(params.hostVersion);

  return {
    info: {
      id,
      name,
      version,
      ownsCompaction: true,
      transcriptSemantics: {
        currentTurnFence: "before-current-turn-entry-v1",
        turnAdvancementIdempotency: "atomic-idempotent-v1",
      },
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
      const tokenBudget = validTokenBudget(assembleParams.tokenBudget) ?? 128_000;
      const isMainAssemble =
        Object.prototype.hasOwnProperty.call(assembleParams, "availableTools") ||
        Object.prototype.hasOwnProperty.call(assembleParams, "citationsMode") ||
        Object.prototype.hasOwnProperty.call(assembleParams, "prompt");
      return assembleOpenVikingSession({
        sessionId: assembleParams.sessionId,
        sessionKey: resolveSessionKey(assembleParams),
        messages: assembleParams.messages,
        prompt: assembleParams.prompt,
        tokenBudget,
        runtimeContext: assembleParams.runtimeContext,
        isMainAssemble,
        cfg,
        getClient,
        logger,
        resolveAgentId,
        rememberSessionAgentId,
        isBypassedSession,
        queryConfigStore,
        traceRecorder,
        diag,
        roughEstimate,
        messageDigest,
        extractAgentMessageText,
        hasAutoRecallBlock,
        prependRecallToLatestUserMessage,
      });
    },

    async commitTurn(commitParams): Promise<{ status: "committed" | "duplicate" }> {
      const { advancementKey, sessionId } = commitParams;
      if (deferredTurnCapture === undefined) {
        // ACKing with an unknown capture owner can discard the only copy in the
        // host outbox. Reject instead; legacy afterTurn remains available.
        throw new Error(`openviking: cannot select turn capture for OpenClaw version ${params.hostVersion ?? "(missing)"}`);
      }
      if (committedTurnKeys.has(advancementKey)) {
        diag("commitTurn_duplicate", sessionId, { advancementKey });
        return { status: "duplicate" };
      }
      const pending = inFlightTurns.get(advancementKey);
      if (pending) {
        await pending;
        return { status: "duplicate" };
      }
      // Publish the promise before capture begins so concurrent deliveries share
      // both success and failure. Completion keys are intentionally process-local.
      const capture = Promise.resolve().then(async () => {
        if (deferredTurnCapture) {
          await afterTurnOpenVikingSession({
            sessionId,
            sessionKey: resolveSessionKey(commitParams),
            messages: commitParams.messages,
            prePromptMessageCount: 0, // Host already supplies the closed-turn range.
            isHeartbeat: commitParams.isHeartbeat,
            tokenBudget: 128_000, // Durable delivery does not carry a token budget.
            throwOnError: true,
            cfg,
            getClient,
            logger,
            resolveAgentId,
            rememberSessionAgentId,
            isBypassedSession,
            diag,
          });
        }
        committedTurnKeys.add(advancementKey);
        if (committedTurnKeys.size > 1024) {
          committedTurnKeys.delete(committedTurnKeys.values().next().value as string);
        }
        diag("commitTurn", sessionId, { advancementKey, deferredTurnCapture });
      });
      inFlightTurns.set(advancementKey, capture);
      try {
        await capture;
        return { status: "committed" };
      } finally {
        inFlightTurns.delete(advancementKey);
      }
    },

    async afterTurn(afterTurnParams): Promise<void> {
      const tokenBudget = validTokenBudget(afterTurnParams.tokenBudget) ?? 128_000;
      await afterTurnOpenVikingSession({
        sessionId: afterTurnParams.sessionId,
        sessionKey: resolveSessionKey(afterTurnParams),
        messages: afterTurnParams.messages,
        prePromptMessageCount: afterTurnParams.prePromptMessageCount,
        isHeartbeat: afterTurnParams.isHeartbeat,
        tokenBudget,
        runtimeContext: afterTurnParams.runtimeContext,
        cfg,
        getClient,
        logger,
        resolveAgentId,
        rememberSessionAgentId,
        isBypassedSession,
        diag,
      });
    },

    async compact(compactParams): Promise<CompactResult> {
      const tokenBudget = validTokenBudget(compactParams.tokenBudget) ?? 128_000;
      return compactOpenVikingSession({
        sessionId: compactParams.sessionId,
        sessionKey: resolveSessionKey(compactParams),
        tokenBudget,
        currentTokenCount: compactParams.currentTokenCount,
        force: compactParams.force,
        compactionTarget: compactParams.compactionTarget,
        customInstructions: compactParams.customInstructions,
        getClient,
        logger,
        resolveAgentId,
        isBypassedSession,
        runtimeCompact: async () => {
          const delegate = await loadRuntimeCompactionDelegate();
          return delegate ? delegate(compactParams) : undefined;
        },
        diag,
      });
    },
  };
}
