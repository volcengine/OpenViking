import { createHash } from "node:crypto";
import { DEFAULT_PHASE2_POLL_TIMEOUT_MS } from "./client.js";
import type { OpenVikingClient, OVMessage } from "./client.js";
import type { MemoryOpenVikingConfig } from "./config.js";
import {
  AUTO_RECALL_SOURCE_MARKER,
  buildAutoRecallContext,
  prepareRecallQuery,
} from "./auto-recall.js";
import {
  compileSessionPatterns,
  getCaptureDecision,
  extractNewTurnMessages,
  shouldBypassSession,
} from "./text-utils.js";
import {
  trimForLog,
  toJsonLog,
} from "./memory-ranking.js";
import { sanitizeToolUseResultPairing } from "./session-transcript-repair.js";

type AgentMessage = {
  role?: string;
  content?: unknown;
  timestamp?: unknown;
};

type ContextEngineInfo = {
  id: string;
  name: string;
  version?: string;
  ownsCompaction: true;
};

type AssembleResult = {
  messages: AgentMessage[];
  estimatedTokens: number;
  systemPromptAddition?: string;
};

type IngestResult = {
  ingested: boolean;
};

export function toRoleId(senderId: string | undefined): string | undefined {
  if (!senderId) {
    return undefined;
  }
  const normalized = senderId
    .trim()
    .replace(/[^a-zA-Z0-9_-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/_+/g, "_");
  return normalized || undefined;
}

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

type ExtractedSender = {
  found: boolean;
  senderId?: string;
};

interface ContextBudgets {
  archiveMemory: number;
  sessionContext: number;
  reserved: number;
}

const BUDGET_UNLIMITED = -1;
const ARCHIVE_BUDGET_RATIO = 0.15;
const ARCHIVE_BUDGET_CAP = 8_000;
const RESERVED_MIN = 20_000;
const RESERVED_RATIO = 0.15;
const ARCHIVE_INDEX_TRIM_LIMIT = 10;

function allocateContextBudget(totalBudget: number, instructionTokens = 0): ContextBudgets {
  const reserveFloor = totalBudget >= RESERVED_MIN * 2 ? RESERVED_MIN : 0;
  const reserved = Math.min(totalBudget, Math.max(totalBudget * RESERVED_RATIO, reserveFloor));
  const usableBudget = Math.max(totalBudget - reserved - instructionTokens, 0);
  const archiveMemory = Math.min(usableBudget * ARCHIVE_BUDGET_RATIO, ARCHIVE_BUDGET_CAP);
  const sessionContext = Math.max(usableBudget - archiveMemory, 0);
  return { archiveMemory, sessionContext, reserved };
}

function roughEstimate(messages: AgentMessage[]): number {
  return Math.ceil(JSON.stringify(messages).length / 4);
}

function msgTokenEstimate(msg: AgentMessage): number {
  const raw = (msg as Record<string, unknown>).content;
  if (typeof raw === "string") return Math.ceil(raw.length / 4);
  if (Array.isArray(raw)) return Math.ceil(JSON.stringify(raw).length / 4);
  return 1;
}

function normalizeTimestamp(value: unknown): string | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    const timestampMs = Math.abs(value) < 100_000_000_000 ? value * 1000 : value;
    return new Date(timestampMs).toISOString();
  }
  return undefined;
}

function pickLatestCreatedAt(messages: AgentMessage[]): string | undefined {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i] as Record<string, unknown>;
    const role = typeof message.role === "string" ? message.role : "";
    if (!role || role === "system") {
      continue;
    }
    const normalized = normalizeTimestamp(message.timestamp);
    if (normalized) {
      return normalized;
    }
  }
  return undefined;
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

function extractRuntimeSenderId(
  runtimeContext: Record<string, unknown> | undefined,
): ExtractedSender {
  if (runtimeContext) {
    const senderId = runtimeContext.senderId;
    if (typeof senderId === "string") {
      const trimmed = senderId.trim();
      if (trimmed) {
        return {
          found: true,
          senderId: trimmed,
        };
      }
    }
  }
  return { found: false };
}

/** OpenClaw session UUID (path-safe on Windows). */
const OPENVIKING_OV_SESSION_UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const WINDOWS_BAD_SESSION_SEGMENT = /[:<>"\\/|?\u0000-\u001f]/;

/**
 * Map OpenClaw session identity to an OpenViking session_id that is safe as a single
 * AGFS path segment on Windows (no `:` etc.). Prefer UUID sessionId when present;
 * otherwise derive a stable sha256 from sessionKey.
 */
export function openClawSessionToOvStorageId(
  sessionId: string | undefined,
  sessionKey: string | undefined,
): string {
  const sid = typeof sessionId === "string" ? sessionId.trim() : "";
  const key = typeof sessionKey === "string" ? sessionKey.trim() : "";

  if (sid && OPENVIKING_OV_SESSION_UUID.test(sid)) {
    return sid.toLowerCase();
  }
  if (key) {
    return createHash("sha256").update(key, "utf8").digest("hex");
  }
  if (sid) {
    if (WINDOWS_BAD_SESSION_SEGMENT.test(sid)) {
      return createHash("sha256").update(`openclaw-session:${sid}`, "utf8").digest("hex");
    }
    return sid;
  }
  throw new Error("openviking: need sessionId or sessionKey for OV session path");
}

/** Normalize a hook/tool session ref (uuid, sessionKey, or already-safe id) for OV storage. */
export function openClawSessionRefToOvStorageId(ref: string): string {
  const t = ref.trim();
  if (!t) {
    throw new Error("openviking: empty session ref");
  }
  if (OPENVIKING_OV_SESSION_UUID.test(t)) {
    return t.toLowerCase();
  }
  if (WINDOWS_BAD_SESSION_SEGMENT.test(t)) {
    return createHash("sha256").update(t, "utf8").digest("hex");
  }
  return t;
}

/**
 * Convert an OpenViking stored message (parts-based format) into one or more
 * OpenClaw AgentMessages (content-blocks format).
 *
 * For assistant messages with ToolParts, this produces:
 * 1. The assistant message with canonical toolCall blocks in its content array
 * 2. A separate toolResult message per ToolPart (carrying tool_output)
 */
export function convertToAgentMessages(msg: { role: string; parts: unknown[] }): AgentMessage[] {
  const parts = msg.parts ?? [];
  const contentBlocks: Record<string, unknown>[] = [];
  const toolCallBlocks: Record<string, unknown>[] = [];
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
    } else if (p.type === "tool") {
      const toolId = typeof p.tool_id === "string" ? p.tool_id : "";
      const toolName = typeof p.tool_name === "string" ? p.tool_name : undefined;
      const status = typeof p.tool_status === "string" ? p.tool_status : "unknown";
      const output = typeof p.tool_output === "string" ? p.tool_output : "";

      if (toolId) {
        // Structured path: emit canonical toolCall + toolResult pair (works for any role)
        toolCallBlocks.push({
          type: "toolCall",
          id: toolId,
          name: toolName ?? "unknown",
          arguments: p.tool_input ?? {},
        });

        const resultText = (status === "completed" || status === "error")
          ? (output || "(no output)")
          : "(interrupted — tool did not complete)";
        const resultPayload: Record<string, unknown> = {
          role: "toolResult",
          toolCallId: toolId,
          content: [{ type: "text", text: resultText }],
          isError: status === "error",
        };
        if (toolName) {
          resultPayload.toolName = toolName;
        }
        toolResults.push(resultPayload as unknown as AgentMessage);
      } else {
        // No tool_id: degrade to text block
        const fallbackName = toolName ?? "unknown";
        const segments = [`[${fallbackName}] (${status})`];
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
    // Assistant: text + toolCall in one message, then toolResults
    result.push({ role: "assistant", content: [...contentBlocks, ...toolCallBlocks] });
    result.push(...toolResults);
  } else {
    // Non-assistant: emit text as original role, then synthesize assistant(toolCall) + toolResult
    const texts = contentBlocks
      .filter((b) => b.type === "text")
      .map((b) => b.text as string);
    if (texts.length > 0) {
      result.push({ role: msg.role, content: texts.join("\n") });
    } else if (toolCallBlocks.length === 0) {
      result.push({ role: msg.role, content: "" });
    }
    if (toolCallBlocks.length > 0) {
      result.push({ role: "assistant", content: toolCallBlocks });
      result.push(...toolResults);
    }
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

function canonicalizeAssistantBlock(block: unknown): unknown {
  if (!block || typeof block !== "object") {
    return block;
  }

  const rec = block as Record<string, unknown>;
  const type = typeof rec.type === "string" ? rec.type : "";
  if (type === "toolCall") {
    if (rec.arguments !== undefined) {
      return rec;
    }
    return {
      ...rec,
      arguments: rec.input ?? rec.toolInput ?? {},
    };
  }

  if (type === "toolUse" || type === "functionCall" || type === "tool_call") {
    return {
      type: "toolCall",
      id: rec.id ?? rec.toolCallId ?? rec.toolUseId,
      name: rec.name,
      arguments: rec.arguments ?? rec.input ?? rec.toolInput ?? {},
    };
  }

  return rec;
}

function canonicalizeAgentMessages(messages: AgentMessage[]): AgentMessage[] {
  let changed = false;
  const next = messages.map((msg) => {
    if (!msg || typeof msg !== "object") {
      return msg;
    }

    if (msg.role === "assistant") {
      const content = Array.isArray(msg.content)
        ? msg.content.map((block) => canonicalizeAssistantBlock(block))
        : typeof msg.content === "string"
          ? [{ type: "text", text: msg.content }]
          : msg.content;

      if (content !== msg.content) {
        changed = true;
        return { ...msg, content };
      }
      return msg;
    }

    if (msg.role === "toolResult") {
      const raw = msg as Record<string, unknown>;
      const toolCallId =
        (typeof raw.toolCallId === "string" && raw.toolCallId) ||
        (typeof raw.toolUseId === "string" && raw.toolUseId) ||
        undefined;
      const toolName =
        typeof raw.toolName === "string" && raw.toolName.trim()
          ? raw.toolName.trim()
          : undefined;

      const nextMsg = {
        ...msg,
        ...(toolCallId ? { toolCallId } : {}),
        ...(toolName ? { toolName } : {}),
      } as AgentMessage;

      if (nextMsg !== msg) {
        changed = true;
      }
      return nextMsg;
    }

    return msg;
  });

  return changed ? next : messages;
}

export function formatMessageFaithful(msg: OVMessage): string {
  const roleTag = `[${msg.role}]`;
  if (!msg.parts || msg.parts.length === 0) {
    return `${roleTag}: (empty)`;
  }

  const sections: string[] = [];
  for (const part of msg.parts) {
    if (!part || typeof part !== "object") continue;
    switch (part.type) {
      case "text":
        if (part.text) sections.push(part.text);
        break;
      case "tool": {
        const status = part.tool_status ?? "unknown";
        const header = `[Tool: ${part.tool_name ?? "unknown"}] (${status})`;
        const inputStr = part.tool_input
          ? `Input: ${JSON.stringify(part.tool_input, null, 2)}`
          : "";
        const outputStr = part.tool_output ? `Output:\n${part.tool_output}` : "";
        sections.push([header, inputStr, outputStr].filter(Boolean).join("\n"));
        break;
      }
      case "context":
        sections.push(
          `[Context: ${part.uri ?? "?"}]${part.abstract ? ` ${part.abstract}` : ""}`,
        );
        break;
      default:
        sections.push(`[${part.type}]: ${JSON.stringify(part)}`);
    }
  }

  return `${roleTag}:\n${sections.join("\n\n")}`;
}

function buildSystemPromptAddition(): string {
  return [
    "## Session Context Guide",
    "",
    "Your conversation history includes two layers:",
    "",
    "1. **[Session History Summary]** — A compressed summary of all prior turns",
    "   in this session. It is organized into structured sections (Key Facts,",
    "   Timeline, People, etc.). Use it for background and continuity.",
    "   The summary is lossy: specific details (exact dates, numbers, names,",
    "   small events) may have been compressed away.",
    "",
    "2. **Active messages** — The most recent uncompressed turns.",
    "",
    "**Rules:**",
    "- When active messages conflict with the Summary, trust active messages",
    "  as the newer source of truth.",
    "- Do not fabricate details the Summary does not state explicitly.",
    "- **CRITICAL: Before answering 'no information' or 'not mentioned',",
    "  you MUST carefully re-read EVERY section of the [Session History Summary].",
    "  The answer may be expressed with different wording than the question.",
    "  Look for synonyms, related facts, and indirect references.**",
    "- If the Summary mentions a topic but lacks the specific detail asked,",
    "  use the `ov_archive_search` tool to grep the original archived messages",
    "  for the exact detail. Try 2-3 different keywords extracted from the question.",
    "- Only conclude information is unavailable AFTER both checking the Summary",
    "  thoroughly AND searching the archives with at least 2 keyword variations.",
  ].join("\n");
}

function buildInstructionPrompt(): { text: string; tokens: number } {
  const text = buildSystemPromptAddition();
  return { text, tokens: Math.ceil(text.length / 4) };
}

function buildArchiveMemory(
  archiveOverview: string | undefined,
  _preAbstracts: Array<{ archive_id: string; abstract: string }>,
  _budget: number,
): { messages: AgentMessage[]; tokens: number } {
  const messages: AgentMessage[] = [];

  if (archiveOverview) {
    messages.push({
      role: "user",
      content: `[Session History Summary]\n${archiveOverview}`,
    });
  }

  return { messages, tokens: roughEstimate(messages) };
}

/** Merge consecutive assistant messages by concatenating their content arrays. */
export function mergeConsecutiveAssistants(messages: AgentMessage[]): AgentMessage[] {
  const result: AgentMessage[] = [];
  for (const msg of messages) {
    const prev = result[result.length - 1];
    if (msg.role === "assistant" && prev?.role === "assistant") {
      const prevContent = Array.isArray(prev.content) ? prev.content : [{ type: "text", text: prev.content }];
      const currContent = Array.isArray(msg.content) ? msg.content : [{ type: "text", text: msg.content }];
      prev.content = [...prevContent, ...currContent] as typeof prev.content;
    } else {
      result.push({ ...msg });
    }
  }
  return result;
}

/**
 * Hoist tool_result blocks to the front of a content array.
 *
 * The Anthropic / Bedrock / Gemini APIs require tool_result blocks to appear
 * at the START of a user message's content array (a tool_result must follow
 * the assistant tool_use that produced it). When mergeConsecutiveUsers
 * merges two user messages, the previous content's text blocks may end up
 * before tool_results from the second message — this function fixes the order.
 *
 * Same pattern as Claude Code's hoistToolResults in src/utils/messages.ts.
 */
function hoistToolResults<T>(content: T[]): T[] {
  const toolResults: T[] = [];
  const others: T[] = [];
  for (const block of content) {
    if (
      block &&
      typeof block === "object" &&
      "type" in block &&
      (block as { type?: string }).type === "tool_result"
    ) {
      toolResults.push(block);
    } else {
      others.push(block);
    }
  }
  return [...toolResults, ...others];
}

/**
 * Merge consecutive user messages by concatenating their content arrays.
 *
 * Mirror of mergeConsecutiveAssistants. Required because Gemini and Anthropic
 * APIs reject consecutive same-role messages with stopReason=stop payloads=0
 * (empty response). Three independent sources can inject role: "user":
 *
 *   1. Archive commit: "[Session History Summary]" via buildArchiveMemory
 *   2. OpenClaw yield events: "[sessions_yield interrupt]" / "Turn yielded. ..."
 *   3. Audio/Telegram metadata: "[Audio] User text: [Telegram <name>...]"
 *
 * Without merging, these can stack into 2-5 consecutive user turns. The
 * 1P Anthropic API would merge server-side, but Bedrock/Gemini won't —
 * we merge client-side for wire-format consistency.
 *
 * Note: this MUST run AFTER sanitizeToolUseResultPairing because that pass
 * may strip orphaned tool_use / tool_result blocks and thereby create new
 * user-user adjacencies that didn't exist in the input.
 *
 * Tracks issue #1724.
 */
export function mergeConsecutiveUsers(messages: AgentMessage[]): AgentMessage[] {
  const result: AgentMessage[] = [];
  for (const msg of messages) {
    const prev = result[result.length - 1];
    if (msg.role === "user" && prev?.role === "user") {
      const prevContent = Array.isArray(prev.content) ? prev.content : [{ type: "text", text: prev.content }];
      const currContent = Array.isArray(msg.content) ? msg.content : [{ type: "text", text: msg.content }];
      prev.content = hoistToolResults([...prevContent, ...currContent]) as typeof prev.content;
    } else {
      result.push({ ...msg });
    }
  }
  return result;
}

/**
 * Defensive role-alternation invariant check.
 *
 * After mergeConsecutiveUsers + mergeConsecutiveAssistants, the message stream
 * should already alternate user/assistant. But sanitizeToolUseResultPairing
 * can in rare cases strip a user_with_tool_result message that was the only
 * thing separating two assistant messages, leaving an assistant-assistant
 * adjacency that upstream merge passes can't fix.
 *
 * When detected, we insert a placeholder user message — matching Claude Code's
 * NO_CONTENT_MESSAGE pattern (see CC src/utils/messages.ts:5375-5388) — to
 * preserve the alternation contract that Gemini / Anthropic require.
 */
export function ensureAlternation(messages: AgentMessage[]): AgentMessage[] {
  const result: AgentMessage[] = [];
  for (const msg of messages) {
    const prev = result[result.length - 1];
    if (prev && prev.role === "assistant" && msg.role === "assistant") {
      result.push({
        role: "user",
        content: "(no content)",
      });
    }
    result.push(msg);
  }
  return result;
}

function buildSessionContext(
  ovMessages: OVMessage[],
  budget: number,
): { messages: AgentMessage[]; tokens: number } {
  const raw = ovMessages.flatMap((m) => convertToAgentMessages(m));
  const messages = mergeConsecutiveAssistants(raw);
  const tokens = roughEstimate(messages);
  if (budget === BUDGET_UNLIMITED || tokens <= budget) {
    return { messages, tokens };
  }
  const trimmed = [...messages];
  while (trimmed.length > 0 && roughEstimate(trimmed) > budget) {
    trimmed.shift();
  }
  return { messages: trimmed, tokens: roughEstimate(trimmed) };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const PHASE2_POLL_INTERVAL_MS = 800;
const PHASE2_POLL_MAX_MS = DEFAULT_PHASE2_POLL_TIMEOUT_MS;

/**
 * After wait=false commit, Phase2 runs on the server. Poll task until completed/failed/timeout
 * so logs show memories_extracted (otherwise it looks like "nothing was saved").
 */
async function pollPhase2ExtractionOutcome(
  getClient: () => Promise<OpenVikingClient>,
  taskId: string,
  agentId: string,
  logger: Logger,
  sessionLabel: string,
): Promise<void> {
  const deadline = Date.now() + PHASE2_POLL_MAX_MS;
  try {
    const client = await getClient();
    while (Date.now() < deadline) {
      await sleep(PHASE2_POLL_INTERVAL_MS);
      const task = await client.getTask(taskId, agentId).catch((e) => {
        logger.warn?.(`openviking: phase2 getTask failed task_id=${taskId}: ${String(e)}`);
        return null;
      });
      if (!task) {
        return;
      }
      const { status } = task;
      if (status === "completed") {
        logger.info(
          `openviking: phase2 completed task_id=${taskId} session=${sessionLabel} ` +
            `result=${toJsonLog(task.result ?? {})}`,
        );
        return;
      }
      if (status === "failed") {
        logger.warn?.(
          `openviking: phase2 failed task_id=${taskId} session=${sessionLabel} error=${task.error ?? "unknown"}`,
        );
        return;
      }
    }
    logger.warn?.(
      `openviking: phase2 poll timeout (${PHASE2_POLL_MAX_MS / 1000}s) task_id=${taskId} session=${sessionLabel} — ` +
        `check GET /api/v1/tasks/${taskId}`,
    );
  } catch (e) {
    logger.warn?.(`openviking: phase2 poll exception task_id=${taskId}: ${String(e)}`);
  }
}

export function createMemoryOpenVikingContextEngine(params: {
  id: string;
  name: string;
  version?: string;
  cfg: Required<MemoryOpenVikingConfig>;
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
    const { sessionKey, ovSessionId: ovId } = resolveSessionIdentity(params);
    if (isBypassedSession({ sessionId, sessionKey })) {
      logger.warn?.(
        `openviking: commit skipped because session is bypassed (sessionId=${sessionId}, sessionKey=${sessionKey ?? "none"})`,
      );
      return false;
    }
    try {
      const client = await getClient();
      rememberSessionAgentId?.({
        sessionId,
        sessionKey,
        ovSessionId: ovId,
      });
      const agentId = resolveAgentId(sessionId, sessionKey, ovId);
      const commitResult = await client.commitSession(ovId, {
        wait: true,
        agentId,
        keepRecentCount: 0,
      });
      const memCount = totalExtractedMemories(commitResult.memories_extracted);
      if (commitResult.status === "failed") {
        logger.warn?.(`openviking: commit Phase 2 failed for session=${sessionId}: ${commitResult.error ?? "unknown"}`);
        return false;
      }
      if (commitResult.status === "timeout") {
        logger.warn?.(`openviking: commit Phase 2 timed out for session=${sessionId}, task_id=${commitResult.task_id ?? "none"}`);
        return false;
      }
      logger.info(
        `openviking: committed OV session=${sessionId} ovId=${ovId}, archived=${commitResult.archived ?? false}, memories=${memCount}, task_id=${commitResult.task_id ?? "none"}, trace_id=${commitResult.trace_id ?? "none"}`,
      );
      return true;
    } catch (err) {
      logger.warn?.(`openviking: commit failed for session=${sessionId}: ${String(err)}`);
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

  function extractRuntimeAgentId(
    runtimeContext: Record<string, unknown> | undefined,
  ): string | undefined {
    if (!runtimeContext) {
      return undefined;
    }
    const agentId = runtimeContext.agentId;
    return typeof agentId === "string" && agentId.trim() ? agentId.trim() : undefined;
  }

  function assemblePassthrough(
    ovSessionId: string,
    reason: string,
    liveMessages: AgentMessage[],
    originalTokens: number,
    extra?: Record<string, unknown>,
  ): AssembleResult {
    diag("assemble_result", ovSessionId, {
      passthrough: true,
      reason,
      outputMessagesCount: liveMessages.length,
      inputTokenEstimate: originalTokens,
      estimatedTokens: originalTokens,
      tokensSaved: 0,
      savingPct: 0,
      ...extra,
    });
    return { messages: liveMessages, estimatedTokens: originalTokens };
  }

  function buildAssembledContext(
    overview: string | undefined,
    preAbstracts: Array<{ archive_id: string; abstract: string }>,
    ovMessages: OVMessage[],
    tokenBudget: number,
    ovSessionId: string,
  ): {
    sanitized: AgentMessage[];
    archive: { messages: AgentMessage[]; tokens: number };
    session: { messages: AgentMessage[]; tokens: number };
    budgets: ContextBudgets;
    instruction: { text: string; tokens: number };
  } {
    const hasArchives = Boolean(overview) || preAbstracts.length > 0;
    const instruction = hasArchives ? buildInstructionPrompt() : { text: "", tokens: 0 };

    // 4-layer context partitioning:
    //   Instruction — system prompt guide (Archive Index / Session History usage)
    //   Archive     — session history summary + per-archive one-line abstracts
    //   Session     — active OV messages converted to AgentMessage format
    //   Reserved    — headroom for model output (not consumed here)
    const budgets = allocateContextBudget(tokenBudget, instruction.tokens);
    const archive = buildArchiveMemory(overview, preAbstracts, budgets.archiveMemory);
    const sessionBudget = Math.max(
      tokenBudget - budgets.reserved - instruction.tokens - archive.tokens,
      0,
    );
    const session = buildSessionContext(ovMessages, sessionBudget);
    const assembled = [...archive.messages, ...session.messages];

    logger.info(
      `openviking: assemble entering session content for ${ovSessionId}: ` +
        JSON.stringify(assembled.map((m) => ({
          role: m.role,
          content: typeof m.content === "string" ? m.content.substring(0, 100) : "[complex]",
        })), null, 2),
    );

    normalizeAssistantContent(assembled);
    const canonical = canonicalizeAgentMessages(assembled);
    // Defense in depth (issue #1724):
    //   1) sanitizeToolUseResultPairing may strip orphaned tool_use/tool_result,
    //      potentially creating new user-user or assistant-assistant adjacencies.
    //   2) mergeConsecutiveUsers fixes user-user (mirror of mergeConsecutiveAssistants
    //      already running inside buildSessionContext).
    //   3) ensureAlternation is a final invariant check for the rare
    //      assistant-assistant case that the merges can't reach.
    const sanitized = ensureAlternation(
      mergeConsecutiveUsers(
        sanitizeToolUseResultPairing(canonical as never[]) as AgentMessage[],
      ),
    );

    return { sanitized, archive, session, budgets, instruction };
  }

  return {
    info: {
      id,
      name,
      version,
      ownsCompaction: true,
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
      const { sessionKey, ovSessionId: OVSessionId } = resolveSessionIdentity(assembleParams);
      const sender = extractRuntimeSenderId(assembleParams.runtimeContext);
      const latestMessage = messages.at(-1);
      const isMainAssemble =
        Object.prototype.hasOwnProperty.call(assembleParams, "availableTools") ||
        Object.prototype.hasOwnProperty.call(assembleParams, "citationsMode") ||
        Object.prototype.hasOwnProperty.call(assembleParams, "prompt");
      const isTransformContextAssemble = !isMainAssemble;

      const originalTokens = roughEstimate(messages);

      rememberSessionAgentId?.({
        sessionId: assembleParams.sessionId,
        sessionKey,
        agentId: extractRuntimeAgentId(assembleParams.runtimeContext),
        ovSessionId: OVSessionId,
      });
      diag("assemble_entry", OVSessionId, {
        messagesCount: messages.length,
        inputTokenEstimate: originalTokens,
        tokenBudget,
        sessionKey: sessionKey ?? null,
        senderIdFound: sender.found,
        senderId: sender.senderId ?? null,
        messages: messageDigest(messages),
      });

      if (isBypassedSession({ sessionId: assembleParams.sessionId, sessionKey })) {
        return assemblePassthrough(OVSessionId, "session_bypassed", messages, originalTokens);
      }

      if (isTransformContextAssemble) {
        if (latestMessage?.role !== "user") {
          return assemblePassthrough(OVSessionId, "transform_context_non_user_tail", messages, originalTokens, {
            latestRole: latestMessage?.role ?? null,
          });
        }
        if (!cfg.autoRecall) {
          return assemblePassthrough(OVSessionId, "transform_context_auto_recall_disabled", messages, originalTokens);
        }
        if (hasAutoRecallBlock(latestMessage)) {
          return assemblePassthrough(OVSessionId, "transform_context_recall_already_injected", messages, originalTokens);
        }

        const recallQuery = prepareRecallQuery(extractAgentMessageText(latestMessage));
        if (!recallQuery.query || recallQuery.query.length < 5) {
          return assemblePassthrough(OVSessionId, "transform_context_empty_recall_query", messages, originalTokens);
        }
        if (recallQuery.truncated) {
          logger.info(
            `openviking: recall query truncated (` +
              `chars=${recallQuery.originalChars}->${recallQuery.finalChars})`,
          );
        }

        try {
          const client = await getClient();
          const routingRef = assembleParams.sessionId ?? sessionKey ?? OVSessionId;
          const agentId = resolveAgentId(routingRef, sessionKey, OVSessionId);
          const recall = await buildAutoRecallContext({
            cfg,
            client,
            agentId,
            queryText: recallQuery.query,
            logger,
            verbose: (message) => logger.info(message),
          });

          if (!recall.block) {
            return assemblePassthrough(OVSessionId, "transform_context_no_recall_hits", messages, originalTokens, {
              memoryCount: recall.memoryCount,
            });
          }

          const withRecall = prependRecallToLatestUserMessage(messages, recall.block);
          const estimatedTokens = roughEstimate(withRecall);
          diag("assemble_result", OVSessionId, {
            passthrough: false,
            phase: "transform_context",
            outputMessagesCount: withRecall.length,
            inputTokenEstimate: originalTokens,
            estimatedTokens,
            autoRecallMemoryCount: recall.memoryCount,
            autoRecallTokens: recall.estimatedTokens,
            messages: messageDigest(withRecall),
          });
          return { messages: withRecall, estimatedTokens };
        } catch (err) {
          logger.warn?.(`openviking: auto-recall failed: ${String(err)}`);
          return assemblePassthrough(OVSessionId, "transform_context_recall_failed", messages, originalTokens, {
            error: String(err),
          });
        }
      }

      try {
        const client = await getClient();
        const routingRef = assembleParams.sessionId ?? sessionKey ?? OVSessionId;
        const agentId = resolveAgentId(routingRef, sessionKey, OVSessionId);
        const ctx = await client.getSessionContext(OVSessionId, tokenBudget, agentId);

        const preAbstracts = ctx?.pre_archive_abstracts ?? [];
        const hasArchives = !!ctx?.latest_archive_overview || preAbstracts.length > 0;
        const activeCount = ctx?.messages?.length ?? 0;

        if (!ctx || (!hasArchives && activeCount === 0)) {
          return assemblePassthrough(OVSessionId, "no_ov_data", messages, originalTokens, {
            archiveCount: 0, activeCount: 0,
          });
        }
        if (!hasArchives && ctx.messages.length < messages.length) {
          return assemblePassthrough(OVSessionId, "ov_msgs_fewer_than_input", messages, originalTokens, {
            archiveCount: 0, activeCount,
          });
        }

        const { sanitized, archive, session, budgets, instruction } = buildAssembledContext(
          ctx.latest_archive_overview,
          preAbstracts,
          ctx.messages,
          tokenBudget,
          OVSessionId,
        );

        if (sanitized.length === 0 && messages.length > 0) {
          return assemblePassthrough(OVSessionId, "sanitized_empty", messages, originalTokens, {
            archiveCount: preAbstracts.length, activeCount,
          });
        }

        const assembledTokens = roughEstimate(sanitized) + instruction.tokens;
        const tokensSaved = originalTokens - assembledTokens;
        const savingPct = originalTokens > 0 ? Math.round((tokensSaved / originalTokens) * 100) : 0;

        diag("assemble_result", OVSessionId, {
          passthrough: false,
          archiveCount: preAbstracts.length,
          activeCount,
          outputMessagesCount: sanitized.length,
          inputTokenEstimate: originalTokens,
          estimatedTokens: assembledTokens,
          tokensSaved,
          savingPct,
          archiveTokens: archive.tokens,
          archiveBudget: budgets.archiveMemory,
          sessionTokens: session.tokens,
          sessionBudget: budgets.sessionContext,
          reservedBudget: budgets.reserved,
          senderIdFound: sender.found,
          senderId: sender.senderId ?? null,
          messages: messageDigest(sanitized),
        });

        return {
          messages: sanitized,
          estimatedTokens: assembledTokens,
          ...(instruction.text ? { systemPromptAddition: instruction.text } : {}),
        };
      } catch (err) {
        logger.warn?.(
          `openviking: assemble failed for session=${OVSessionId}, ` +
            `tokenBudget=${tokenBudget}, agentId=${resolveAgentId(OVSessionId)}: ${String(err)}`,
        );
        diag("assemble_error", OVSessionId, {
          error: String(err),
          tokenBudget,
          agentId: resolveAgentId(OVSessionId),
          senderIdFound: sender.found,
          senderId: sender.senderId ?? null,
        });
        return { messages, estimatedTokens: roughEstimate(messages) };
      }
    },

    async afterTurn(afterTurnParams): Promise<void> {
      if (!cfg.autoCapture) {
        return;
      }

      if (afterTurnParams.isHeartbeat) {
        return;
      }

      try {
        const sender = extractRuntimeSenderId(afterTurnParams.runtimeContext);
        const { sessionKey, ovSessionId: OVSessionId } = resolveSessionIdentity(afterTurnParams);
        const runtimeAgentId = extractRuntimeAgentId(afterTurnParams.runtimeContext);
        if (runtimeAgentId) {
          rememberSessionAgentId?.({
            agentId: runtimeAgentId,
            sessionId: afterTurnParams.sessionId,
            sessionKey,
            ovSessionId: OVSessionId,
          });
        }
        const routingRef =
          afterTurnParams.sessionId ?? sessionKey ?? OVSessionId;
        const agentId = resolveAgentId(routingRef, sessionKey, OVSessionId);

        if (isBypassedSession({ sessionId: afterTurnParams.sessionId, sessionKey })) {
          diag("afterTurn_skip", OVSessionId, {
            reason: "session_bypassed",
            totalMessages: afterTurnParams.messages?.length ?? 0,
            senderIdFound: sender.found,
            senderId: sender.senderId ?? null,
          });
          return;
        }

        const messages = afterTurnParams.messages ?? [];
        if (messages.length === 0) {
          diag("afterTurn_skip", OVSessionId, {
            reason: "no_messages",
            totalMessages: 0,
            senderIdFound: sender.found,
            senderId: sender.senderId ?? null,
          });
          return;
        }

        const start =
          typeof afterTurnParams.prePromptMessageCount === "number" &&
          afterTurnParams.prePromptMessageCount >= 0
            ? afterTurnParams.prePromptMessageCount
            : 0;

        const { messages: extractedMessages, newCount } = extractNewTurnMessages(messages, start);

        if (extractedMessages.length === 0) {
          diag("afterTurn_skip", OVSessionId, {
            reason: "no_new_turn_messages",
            totalMessages: messages.length,
            prePromptMessageCount: start,
            senderIdFound: sender.found,
            senderId: sender.senderId ?? null,
          });
          return;
        }

        const turnMessages = messages.slice(start) as AgentMessage[];
        const newMessages = turnMessages.filter((m: any) => {
          const r = (m as Record<string, unknown>).role as string;
          return r === "user" || r === "assistant";
        }) as AgentMessage[];
        const newMsgFull = messageDigest(newMessages);
        const newTurnTokens = newMsgFull.reduce((s, d) => s + d.tokens, 0);

        diag("afterTurn_entry", OVSessionId, {
          totalMessages: messages.length,
          newMessageCount: newCount,
          prePromptMessageCount: start,
          newTurnTokens,
          senderIdFound: sender.found,
          senderId: sender.senderId ?? null,
          messages: newMsgFull,
        });

        const client = await getClient();
        const createdAt = pickLatestCreatedAt(turnMessages);
        const senderRoleId = toRoleId(sender.senderId);
        // 发送结构化消息：统一 role 为 user，通过 parts 区分类型
        for (const msg of extractedMessages) {
          const ovParts = msg.parts.map((part) => {
            if (part.type === "text") {
              // 清理 relevant-memories 块
              const cleaned = part.text
                .replace(/<relevant-memories>[\s\S]*?<\/relevant-memories>/gi, " ")
                .replace(/\s+/g, " ")
                .trim();
              return { type: "text" as const, text: cleaned };
            } else {
              return {
                type: "tool" as const,
                tool_id: part.toolCallId,
                tool_name: part.toolName,
                tool_input: part.toolInput,
                tool_output: part.toolOutput,
                tool_status: part.toolStatus,
              };
            }
          });

          if (ovParts.length > 0) {
            await client.addSessionMessage(
              OVSessionId,
              msg.role, // 统一是 "user"
              ovParts,
              agentId,
              createdAt,
              msg.role === "user" ? senderRoleId : undefined,
            );
          }
        }

        const session = await client.getSession(OVSessionId, agentId);
        const pendingTokens = session.pending_tokens ?? 0;

        if (pendingTokens < cfg.commitTokenThreshold) {
          diag("afterTurn_skip", OVSessionId, {
            reason: "below_threshold",
            pendingTokens,
            commitTokenThreshold: cfg.commitTokenThreshold,
            senderIdFound: sender.found,
            senderId: sender.senderId ?? null,
          });
          return;
        }

        const commitResult = await client.commitSession(OVSessionId, {
          wait: false,
          agentId,
          keepRecentCount: cfg.commitKeepRecentCount,
        });
        logger.info(
          `openviking: committed session=${OVSessionId}, ` +
            `status=${commitResult.status}, archived=${commitResult.archived ?? false}, ` +
            `task_id=${commitResult.task_id ?? "none"}, trace_id=${commitResult.trace_id ?? "none"}`,
        );

        diag("afterTurn_commit", OVSessionId, {
          pendingTokens,
          commitTokenThreshold: cfg.commitTokenThreshold,
          status: commitResult.status,
          archived: commitResult.archived ?? false,
          taskId: commitResult.task_id ?? null,
          extractedMemories: totalExtractedMemories(commitResult.memories_extracted),
          senderIdFound: sender.found,
          senderId: sender.senderId ?? null,
        });
        if (commitResult.task_id && cfg.logFindRequests) {
          logger.info(
            `openviking: Phase2 memory extraction runs asynchronously on the server (task_id=${commitResult.task_id}). ` +
              "memories_extracted appears only after that task completes — not in this immediate response.",
          );
          void pollPhase2ExtractionOutcome(
            getClient,
            commitResult.task_id,
            agentId,
            logger,
            OVSessionId,
          );
        }
      } catch (err) {
        logger.warn?.(`openviking: afterTurn failed: ${String(err)}`);
        const sender = extractRuntimeSenderId(afterTurnParams.runtimeContext);
        diag("afterTurn_error", afterTurnParams.sessionId ?? "(unknown)", {
          error: String(err),
          senderIdFound: sender.found,
          senderId: sender.senderId ?? null,
        });
      }
    },

    async compact(compactParams): Promise<CompactResult> {
      const { sessionKey, ovSessionId: OVSessionId } = resolveSessionIdentity(compactParams);
      const tokenBudget = validTokenBudget(compactParams.tokenBudget) ?? 128_000;
      diag("compact_entry", OVSessionId, {
        tokenBudget,
        force: compactParams.force ?? false,
        currentTokenCount: compactParams.currentTokenCount ?? null,
        compactionTarget: compactParams.compactionTarget ?? null,
        hasCustomInstructions: typeof compactParams.customInstructions === "string" &&
          compactParams.customInstructions.trim().length > 0,
      });

      if (isBypassedSession({ sessionId: compactParams.sessionId, sessionKey })) {
        diag("compact_result", OVSessionId, {
          ok: true,
          compacted: false,
          reason: "session_bypassed",
        });
        return {
          ok: true,
          compacted: false,
          reason: "session_bypassed",
        };
      }

      const client = await getClient();
      const agentId = resolveAgentId(compactParams.sessionId, sessionKey, OVSessionId);
      const tokensBeforeOriginal = validTokenBudget(compactParams.currentTokenCount);
      let preCommitEstimatedTokens: number | undefined;
      if (typeof tokensBeforeOriginal !== "number") {
        try {
          const preCtx = await client.getSessionContext(OVSessionId, tokenBudget, agentId);
          if (
            typeof preCtx.estimatedTokens === "number" &&
            Number.isFinite(preCtx.estimatedTokens)
          ) {
            preCommitEstimatedTokens = preCtx.estimatedTokens;
          }
        } catch (preCtxErr) {
          logger.info(
            `openviking: compact pre-ctx fetch failed for session=${OVSessionId}, ` +
              `tokenBudget=${tokenBudget}, agentId=${agentId}: ${String(preCtxErr)}`,
          );
        }
      }

      const tokensBefore = tokensBeforeOriginal ?? preCommitEstimatedTokens ?? -1;

      try {
        logger.info(
          `openviking: compact committing session=${OVSessionId} (wait=true, tokenBudget=${tokenBudget})`,
        );
        const commitResult = await client.commitSession(OVSessionId, {
          wait: true,
          agentId,
          keepRecentCount: 0,
        });
        const memCount = totalExtractedMemories(commitResult.memories_extracted);

        if (commitResult.status === "failed") {
          logger.warn?.(
            `openviking: compact commit Phase 2 failed for session=${OVSessionId}: ${commitResult.error ?? "unknown"}`,
          );
          diag("compact_result", OVSessionId, {
            ok: false,
            compacted: false,
            reason: "commit_failed",
            status: commitResult.status,
            archived: commitResult.archived ?? false,
            taskId: commitResult.task_id ?? null,
            error: commitResult.error ?? null,
          });
          return {
            ok: false,
            compacted: false,
            reason: "commit_failed",
            result: {
              summary: "",
              firstKeptEntryId: "",
              tokensBefore: tokensBefore,
              tokensAfter: undefined,
              details: {
                commit: commitResult,
              },
            },
          };
        }

        if (commitResult.status === "timeout") {
          logger.warn?.(
            `openviking: compact commit Phase 2 timed out for session=${OVSessionId}, task_id=${commitResult.task_id ?? "none"}`,
          );
          diag("compact_result", OVSessionId, {
            ok: false,
            compacted: false,
            reason: "commit_timeout",
            status: commitResult.status,
            archived: commitResult.archived ?? false,
            taskId: commitResult.task_id ?? null,
          });
          return {
            ok: false,
            compacted: false,
            reason: "commit_timeout",
            result: {
              summary: "",
              firstKeptEntryId: "",
              tokensBefore: tokensBefore,
              tokensAfter: undefined,
              details: {
                commit: commitResult,
              },
            },
          };
        }

        logger.info(
          `openviking: compact committed session=${OVSessionId}, archived=${commitResult.archived ?? false}, memories=${memCount}, task_id=${commitResult.task_id ?? "none"}, trace_id=${commitResult.trace_id ?? "none"}`,
        );

        if (!commitResult.archived) {
          logger.info(
            `openviking: compact no archive for session=${OVSessionId}, ` +
              `tokensBefore=${tokensBefore}, tokensAfter=${tokensBefore}`,
          );
          diag("compact_result", OVSessionId, {
            ok: true,
            compacted: false,
            reason: "commit_no_archive",
            status: commitResult.status,
            archived: commitResult.archived ?? false,
            taskId: commitResult.task_id ?? null,
            memories: memCount,
            tokensBefore: tokensBefore,
          });
          return {
            ok: true,
            compacted: false,
            reason: "commit_no_archive",
            result: {
              summary: "",
              tokensBefore: tokensBefore,
              tokensAfter: tokensBefore >= 0 ? tokensBefore : undefined,
              details: {
                commit: commitResult,
              },
            },
          };
        }

        let summary = "";
        let firstKeptEntryId = commitResult.archive_uri?.split("/").pop() ?? "";
        let tokensAfter: number | undefined;
        let contextFetchError: string | undefined;

        let ctx: Awaited<ReturnType<typeof client.getSessionContext>> | undefined;
        try {
          ctx = await client.getSessionContext(OVSessionId, tokenBudget, agentId);
          // 打印完整的 getSessionContext 结果
          logger.info(
            `openviking: compact getSessionContext raw result for ${OVSessionId}: ` +
              JSON.stringify(ctx, null, 2),
          );
          if (typeof ctx.latest_archive_overview === "string") {
            summary = ctx.latest_archive_overview.trim();
          }
          if (
            typeof ctx.estimatedTokens === "number" &&
            Number.isFinite(ctx.estimatedTokens)
          ) {
            tokensAfter = ctx.estimatedTokens;
          }
          // 打印 compact 后重新写入 session 的完整内容
          logger.info(
            `openviking: compact restored session content for ${OVSessionId}: ` +
              `messages=${ctx.messages?.length ?? 0}, ` +
              `latestArchiveOverview=${summary.length > 0 ? "present" : "empty"} (${summary.length} chars), ` +
              `preArchiveAbstracts=${ctx.pre_archive_abstracts?.length ?? 0}, ` +
              `estimatedTokens=${ctx.estimatedTokens}`,
          );
          if (summary.length > 0) {
            logger.info(
              `openviking: compact latest_archive_overview for ${OVSessionId}: ${summary.substring(0, 200)}...`,
            );
          }
          if (ctx.messages && ctx.messages.length > 0) {
            // 打印所有消息的 role 和 content 摘要
            const msgSummary = ctx.messages.map((m: { role?: string; content?: string; parts?: Array<{ type?: string; text?: string }> }) => {
              const role = m.role ?? "unknown";
              let textPreview = "";
              if (m.content) {
                textPreview = m.content.substring(0, 80);
              } else if (m.parts && m.parts.length > 0) {
                const textPart = m.parts.find((p: { type?: string }) => p.type === "text");
                textPreview = textPart?.text?.substring(0, 80) ?? JSON.stringify(m.parts).substring(0, 80);
              }
              return { role, textPreview };
            });
            logger.info(
              `openviking: compact restored messages for ${OVSessionId}: ` +
                JSON.stringify(msgSummary),
            );
          }
        } catch (ctxErr) {
          contextFetchError = String(ctxErr);
          logger.info(
            `openviking: compact context fetch failed for session=${OVSessionId}, ` +
              `tokenBudget=${tokenBudget}, agentId=${agentId}: ${contextFetchError}`,
          );
        }

        logger.info(
          `openviking: compact tokens session=${OVSessionId}, ` +
            `tokensBefore=${tokensBefore}, tokensAfter=${tokensAfter ?? "unknown"}, ` +
            `latestArchiveId=${firstKeptEntryId || "none"}`,
        );

        diag("compact_result", OVSessionId, {
          ok: true,
          compacted: true,
          reason: "commit_completed",
          status: commitResult.status,
          archived: commitResult.archived ?? false,
          taskId: commitResult.task_id ?? null,
          memories: memCount,
          tokensBefore: tokensBefore,
          tokensAfter: tokensAfter ?? null,
          latestArchiveId: firstKeptEntryId || null,
          summaryPresent: summary.length > 0,
        });
        return {
          ok: true,
          compacted: true,
          reason: "commit_completed",
          result: {
            summary,
            firstKeptEntryId,
            tokensBefore,
            tokensAfter,
            details: contextFetchError
              ? {
                  commit: commitResult,
                  contextError: contextFetchError,
                }
              : {
                  commit: commitResult,
                },
          },
        };
      } catch (err) {
        const errorMessage = String(err);
        if (errorMessage.includes("[NOT_FOUND]") && errorMessage.includes("Session not found")) {
          logger.info(
            `openviking: compact skipped because OV session does not exist ` +
              `(session=${OVSessionId}, agentId=${agentId})`,
          );
          diag("compact_result", OVSessionId, {
            ok: true,
            compacted: false,
            reason: "session_not_found",
            error: errorMessage,
          });
          return {
            ok: true,
            compacted: false,
            reason: "session_not_found",
          };
        }
        logger.warn?.(`openviking: compact commit failed for session=${OVSessionId}: ${errorMessage}`);
        diag("compact_error", OVSessionId, {
          error: errorMessage,
        });
        return {
          ok: false,
          compacted: false,
          reason: "commit_error",
          result: {
            summary: "",
            firstKeptEntryId: "",
            tokensBefore: tokensBefore,
            tokensAfter: undefined,
            details: {
              error: errorMessage,
            },
          },
        };
      }
    },
  };
}
