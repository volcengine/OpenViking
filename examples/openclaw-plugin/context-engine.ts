import type { OpenVikingClient } from "./client.js";
import type { MemoryOpenVikingConfig } from "./config.js";
import {
  getCaptureDecision,
  extractNewTurnTexts,
} from "./text-utils.js";
import {
  trimForLog,
  toJsonLog,
} from "./memory-ranking.js";
import { sanitizeToolUseResultPairing } from "./session-transcript-repair.js";

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
  /** Commit (extract + archive) then delete the OV session, so a fresh one is created on next use. */
  commitOVSession: (sessionKey: string) => Promise<void>;
};

type Logger = {
  info: (msg: string) => void;
  warn?: (msg: string) => void;
  error: (msg: string) => void;
};

function estimateTokens(messages: AgentMessage[]): number {
  return Math.max(1, messages.length * 80);
}

function roughEstimate(messages: AgentMessage[]): number {
  return Math.ceil(JSON.stringify(messages).length / 4);
}

function totalExtractedMemories(memories?: Record<string, number>): number {
  if (!memories || typeof memories !== "object") {
    return 0;
  }
  return Object.values(memories).reduce((sum, count) => sum + (count ?? 0), 0);
}

function validTokenBudget(raw: unknown): number | undefined {
  if (typeof raw === "number" && Number.isFinite(raw) && raw > 0) {
    return raw;
  }
  return undefined;
}

/**
 * Convert an OpenViking stored message (parts-based format) into one or more
 * OpenClaw AgentMessages (content-blocks format).
 *
 * For assistant messages with ToolParts, this produces:
 * 1. The assistant message with toolUse blocks in its content array
 * 2. A separate toolResult message per ToolPart (carrying tool_output)
 */
function convertToAgentMessages(msg: { role: string; parts: unknown[] }): AgentMessage[] {
  const parts = msg.parts ?? [];
  const contentBlocks: Record<string, unknown>[] = [];
  const toolResults: AgentMessage[] = [];

  for (const part of parts) {
    if (!part || typeof part !== "object") continue;
    const p = part as Record<string, unknown>;

    if (p.type === "text" && typeof p.text === "string") {
      contentBlocks.push({ type: "text", text: p.text });
    } else if (p.type === "context") {
      if (typeof p.abstract === "string" && p.abstract) {
        contentBlocks.push({ type: "text", text: p.abstract });
      }
    } else if (p.type === "tool" && msg.role === "assistant") {
      const toolId = typeof p.tool_id === "string" ? p.tool_id : "";
      const toolName = typeof p.tool_name === "string" ? p.tool_name : "unknown";

      if (toolId) {
        contentBlocks.push({
          type: "toolUse",
          id: toolId,
          name: toolName,
          input: p.tool_input ?? {},
        });

        const status = typeof p.tool_status === "string" ? p.tool_status : "";
        const output = typeof p.tool_output === "string" ? p.tool_output : "";

        if (status === "completed" || status === "error") {
          toolResults.push({
            role: "toolResult",
            toolCallId: toolId,
            toolName,
            content: [{ type: "text", text: output || "(no output)" }],
            isError: status === "error",
          } as unknown as AgentMessage);
        } else {
          toolResults.push({
            role: "toolResult",
            toolCallId: toolId,
            toolName,
            content: [{ type: "text", text: "(interrupted — tool did not complete)" }],
            isError: false,
          } as unknown as AgentMessage);
        }
      } else {
        // No tool_id: degrade to text block to preserve information.
        // Cannot emit toolUse/toolResult without a valid id.
        const status = typeof p.tool_status === "string" ? p.tool_status : "unknown";
        const output = typeof p.tool_output === "string" ? p.tool_output : "";
        const segments = [`[${toolName}] (${status})`];
        if (p.tool_input) {
          try {
            segments.push(`Input: ${JSON.stringify(p.tool_input)}`);
          } catch {
            // non-serializable input, skip
          }
        }
        if (output) {
          segments.push(`Output: ${output}`);
        }
        contentBlocks.push({ type: "text", text: segments.join("\n") });
      }
    }
  }

  const result: AgentMessage[] = [];

  if (msg.role === "assistant") {
    result.push({ role: msg.role, content: contentBlocks });
    result.push(...toolResults);
  } else {
    const texts = contentBlocks
      .filter((b) => b.type === "text")
      .map((b) => b.text as string);
    result.push({ role: msg.role, content: texts.join("\n") || "" });
  }

  return result;
}

function normalizeAssistantContent(messages: AgentMessage[]): void {
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    if (msg?.role === "assistant" && typeof msg.content === "string") {
      messages[i] = {
        ...msg,
        content: [{ type: "text", text: msg.content }],
      };
    }
  }
}

function buildSystemPromptAddition(): string {
  return [
    "## Compressed Context",
    "",
    "The conversation history above includes compressed session summaries",
    '(marked as "# Session Summary"). These summaries contain condensed',
    "information from earlier parts of the conversation.",
    "",
    "**Important:**",
    "- Summaries are compressed context — maps to details, not the details",
    "  themselves.",
    "- For precision questions (exact commands, file paths, timestamps,",
    "  config values): state that the information comes from a summary and",
    "  may need verification.",
    "- Do not fabricate specific details from compressed summaries.",
  ].join("\n");
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

function formatMessagesForLog(label: string, messages: AgentMessage[]): string {
  const lines: string[] = [`===== ${label} (${messages.length} msgs) =====`];
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i] as Record<string, unknown>;
    const role = msg.role ?? "?";
    const raw = msg.content;
    let text: string;
    if (typeof raw === "string") {
      text = raw;
    } else if (Array.isArray(raw)) {
      text = (raw as Record<string, unknown>[])
        .map((b) => {
          if (b.type === "text") return b.text;
          if (b.type === "toolUse") return `[toolUse: ${b.name}]`;
          if (b.type === "toolResult") return `[toolResult]`;
          return `[${b.type}]`;
        })
        .join("\n");
    } else {
      text = JSON.stringify(raw, null, 2);
    }
    lines.push(`--- [${i}] ${role} ---`);
    lines.push(String(text));
  }
  lines.push(`===== /${label} =====`);
  return lines.join("\n");
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

  async function doCommitOVSession(sessionKey: string): Promise<void> {
    try {
      const client = await getClient();
      const agentId = resolveAgentId(sessionKey);
      const commitResult = await client.commitSession(sessionKey, { wait: true, agentId });
      logger.info(
        `openviking: committed OV session for sessionKey=${sessionKey}, archived=${commitResult.archived ?? false}, memories=${totalExtractedMemories(commitResult.memories_extracted)}, task_id=${commitResult.task_id ?? "none"}`,
      );
      await client.deleteSession(sessionKey, agentId).catch(() => {});
    } catch (err) {
      warnOrInfo(logger, `openviking: commit failed for sessionKey=${sessionKey}: ${String(err)}`);
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
      const { messages } = assembleParams;
      const tokenBudget = validTokenBudget(assembleParams.tokenBudget) ?? 128_000;

      const originalTokens = roughEstimate(messages);
      logger.info(formatMessagesForLog(`ORIGINAL CONTEXT (openclaw raw) msgs=${messages.length} ~${originalTokens} tokens`, messages));

      try {
        const client = await getClient();
        const OVSessionId = assembleParams.sessionId;
        const agentId = resolveAgentId(OVSessionId);
        const ctx = await client.getContextForAssemble(
          OVSessionId,
          tokenBudget,
          agentId,
        );
        const archiveCount = ctx?.archives?.length ?? 0;
        const activeCount = ctx?.messages?.length ?? 0;
        logger.info(`openviking: assemble OV ctx archives=${archiveCount} active=${activeCount}`);

        if (!ctx || (archiveCount === 0 && activeCount === 0)) {
          logger.info("openviking: assemble passthrough (no OV data)");
          return { messages, estimatedTokens: roughEstimate(messages) };
        }

        if (ctx.archives.length === 0 && ctx.messages.length < messages.length) {
          logger.info(`openviking: assemble passthrough (OV msgs=${ctx.messages.length} < input msgs=${messages.length})`);
          return { messages, estimatedTokens: roughEstimate(messages) };
        }

        const assembled: AgentMessage[] = [
          ...ctx.archives.map((a) => ({ role: "user" as const, content: a.overview })),
          ...ctx.messages.flatMap((m) => convertToAgentMessages(m)),
        ];

        normalizeAssistantContent(assembled);
        const sanitized = sanitizeToolUseResultPairing(assembled as never[]) as AgentMessage[];

        if (sanitized.length === 0 && messages.length > 0) {
          logger.info("openviking: assemble passthrough (sanitized=0, falling back to original)");
          return { messages, estimatedTokens: roughEstimate(messages) };
        }

        const assembledTokens = roughEstimate(sanitized);
        logger.info(formatMessagesForLog(`ASSEMBLED CONTEXT (openviking) ~${assembledTokens} tokens (ovEstimate=${ctx.estimatedTokens})`, sanitized));

        return {
          messages: sanitized,
          estimatedTokens: ctx.estimatedTokens,
          ...(ctx.archives.length > 0
            ? { systemPromptAddition: buildSystemPromptAddition() }
            : {}),
        };
      } catch {
        return { messages, estimatedTokens: roughEstimate(messages) };
      }
    },

    async afterTurn(afterTurnParams): Promise<void> {
      if (!cfg.autoCapture) {
        return;
      }

      try {
        const sessionKey = extractSessionKey(afterTurnParams.runtimeContext);
        const OVSessionId = sessionKey ?? afterTurnParams.sessionId;
        const agentId = resolveAgentId(OVSessionId);

        const messages = afterTurnParams.messages ?? [];
        if (messages.length === 0) {
          logger.info("openviking: afterTurn skipped (messages=0)");
          return;
        }

        const start =
          typeof afterTurnParams.prePromptMessageCount === "number" &&
          afterTurnParams.prePromptMessageCount >= 0
            ? afterTurnParams.prePromptMessageCount
            : 0;

        const { texts: newTexts, newCount } = extractNewTurnTexts(messages, start);

        if (newTexts.length === 0) {
          logger.info("openviking: afterTurn skipped (no new user/assistant messages)");
          return;
        }

        // Always store messages into OV session so assemble can retrieve them.
        // Capture decision only controls whether we trigger commit (archive+extract).
        const client = await getClient();
        const turnText = newTexts.join("\n");
        const sanitized = turnText.replace(/<relevant-memories>[\s\S]*?<\/relevant-memories>/gi, " ").replace(/\s+/g, " ").trim();

        if (sanitized) {
          await client.addSessionMessage(OVSessionId, "user", sanitized, agentId);
          logger.info(
            `openviking: afterTurn stored ${newCount} msgs in session=${OVSessionId} (${sanitized.length} chars)`,
          );
        } else {
          logger.info("openviking: afterTurn skipped store (sanitized text empty)");
          return;
        }

        // Capture decision: controls commit (archive + memory extraction)
        const decision = getCaptureDecision(turnText, cfg.captureMode, cfg.captureMaxLength);
        logger.info(
          `openviking: capture-check shouldCapture=${String(decision.shouldCapture)} reason=${decision.reason}`,
        );

        if (!decision.shouldCapture) {
          logger.info("openviking: afterTurn skipped commit (capture decision rejected)");
          return;
        }

        const session = await client.getSession(OVSessionId, agentId);
        const pendingTokens = session.pending_tokens ?? 0;

        if (pendingTokens < cfg.commitTokenThreshold) {
          logger.info(
            `openviking: pending_tokens=${pendingTokens}/${cfg.commitTokenThreshold} in session=${OVSessionId}, deferring commit`,
          );
          return;
        }

        logger.info(
          `openviking: committing session=${OVSessionId} (wait=false), pendingTokens=${pendingTokens}, threshold=${cfg.commitTokenThreshold}`,
        );
        const commitResult = await client.commitSession(OVSessionId, { wait: false, agentId });
        logger.info(
          `openviking: committed session=${OVSessionId}, ` +
            `status=${commitResult.status}, archived=${commitResult.archived ?? false}, ` +
            `task_id=${commitResult.task_id ?? "none"} ${toJsonLog({ captured: [trimForLog(turnText, 260)] })}`,
        );
      } catch (err) {
        warnOrInfo(logger, `openviking: afterTurn failed: ${String(err)}`);
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
