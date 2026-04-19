import type { CaptureMode } from "./client.js";

export const MEMORY_TRIGGERS = [
  /remember|preference|prefer|important|decision|decided|always|never/i,
  /记住|偏好|喜欢|喜爱|崇拜|讨厌|害怕|重要|决定|总是|永远|优先|习惯|爱好|擅长|最爱|不喜欢/i,
  /[\w.-]+@[\w.-]+\.\w+/,
  /\+\d{10,}/,
  /(?:我|my)\s*(?:是|叫|名字|name|住在|live|来自|from|生日|birthday|电话|phone|邮箱|email)/i,
  /(?:我|i)\s*(?:喜欢|崇拜|讨厌|害怕|擅长|不会|爱|恨|想要|需要|希望|觉得|认为|相信)/i,
  /(?:favorite|favourite|love|hate|enjoy|dislike|admire|idol|fan of)/i,
];

const CJK_CHAR_REGEX = /[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uac00-\ud7af]/;
const RELEVANT_MEMORIES_BLOCK_RE = /<relevant-memories>[\s\S]*?<\/relevant-memories>/gi;
const CONVERSATION_METADATA_BLOCK_RE =
  /(?:^|\n)\s*(?:Conversation info|Conversation metadata|会话信息|对话信息)\s*(?:\([^)]+\))?\s*:\s*```[\s\S]*?```/gi;
/** Strips "Sender (untrusted metadata): ```json ... ```" so capture sends clean text to OpenViking extract. */
const SENDER_METADATA_BLOCK_RE = /Sender\s*\([^)]*\)\s*:\s*```[\s\S]*?```/gi;
const FENCED_JSON_BLOCK_RE = /```json\s*([\s\S]*?)```/gi;
const METADATA_JSON_KEY_RE =
  /"(session|sessionid|sessionkey|conversationid|channel|sender|userid|agentid|timestamp|timezone)"\s*:/gi;
const LEADING_TIMESTAMP_PREFIX_RE = /^\s*(?!\[\[)\[(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\s+)?(?:\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{2,4})(?:[T\s]+\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|\s*[A-Z]{1,5}(?:[+-]\d{1,2})?)?)?\s*\]\s*/i;
const COMPACTED_SYSTEM_MSG_RE = /^System:\s*\[.*?\]\s*Compacted\s*(.+)$/i;
const COMMAND_TEXT_RE = /^\/[a-z0-9_-]{1,64}\b/i;
const NON_CONTENT_TEXT_RE = /^[\p{P}\p{S}\s]+$/u;
const SUBAGENT_CONTEXT_RE = /^\s*\[Subagent Context\]/i;
const MEMORY_INTENT_RE = /记住|记下|remember|save|store|偏好|preference|规则|rule|事实|fact/i;
const QUESTION_CUE_RE =
  /[?？]|\b(?:what|when|where|who|why|how|which|can|could|would|did|does|is|are)\b|^(?:请问|能否|可否|怎么|如何|什么时候|谁|什么|哪|是否)/i;
export const CAPTURE_LIMIT = 3;

function resolveCaptureMinLength(text: string): number {
  return CJK_CHAR_REGEX.test(text) ? 4 : 10;
}

function looksLikeMetadataJsonBlock(content: string): boolean {
  const matchedKeys = new Set<string>();
  const matches = content.matchAll(METADATA_JSON_KEY_RE);
  for (const match of matches) {
    const key = (match[1] ?? "").toLowerCase();
    if (key) {
      matchedKeys.add(key);
    }
  }
  return matchedKeys.size >= 3;
}

const HEARTBEAT_RE = /\bHEARTBEAT(?:\.md|_OK)\b/;

export function sanitizeUserTextForCapture(text: string): string {
  // 过滤 HEARTBEAT 健康检查消息
  if (HEARTBEAT_RE.test(text)) {
    return "";
  }
  // 处理 Compactor 系统消息，提取实际用户输入
  // 格式: "System: [时间] Compacted ... Context ... [时间] 实际内容"
  if (COMPACTED_SYSTEM_MSG_RE.test(text)) {
    const match = text.match(COMPACTED_SYSTEM_MSG_RE);
    if (match) {
      return match[1].replace(/\s+/g, " ").trim();
    }
    return "";
  }
  return text
    .replace(RELEVANT_MEMORIES_BLOCK_RE, " ")
    .replace(CONVERSATION_METADATA_BLOCK_RE, " ")
    .replace(SENDER_METADATA_BLOCK_RE, " ")
    .replace(SUBAGENT_CONTEXT_RE, " ")
    .replace(FENCED_JSON_BLOCK_RE, (full, inner) =>
      looksLikeMetadataJsonBlock(String(inner ?? "")) ? " " : full,
    )
    .replace(LEADING_TIMESTAMP_PREFIX_RE, "")
    .replace(/\u0000/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function looksLikeQuestionOnlyText(text: string): boolean {
  if (!QUESTION_CUE_RE.test(text) || MEMORY_INTENT_RE.test(text)) {
    return false;
  }
  // Multi-speaker transcripts often contain many "?" but should still be captured.
  const speakerTags = text.match(/[A-Za-z\u4e00-\u9fa5]{2,20}:\s/g) ?? [];
  if (speakerTags.length >= 2 || text.length > 280) {
    return false;
  }
  return true;
}

export function compileSessionPattern(pattern: string): RegExp {
  const escaped = pattern
    .replace(/[.+^${}()|[\]\\]/g, "\\$&")
    .replace(/\*\*/g, "\u0000")
    .replace(/\*/g, "[^:]*")
    .replace(/\u0000/g, ".*");
  return new RegExp(`^${escaped}$`);
}

export function compileSessionPatterns(patterns: string[]): RegExp[] {
  return patterns.map((pattern) => compileSessionPattern(pattern));
}

export function matchesSessionPattern(sessionRef: string, patterns: RegExp[]): boolean {
  return patterns.some((pattern) => pattern.test(sessionRef));
}

export function resolveSessionPatternCandidate(params: {
  sessionId?: string;
  sessionKey?: string;
}): string | undefined {
  const sessionKey = typeof params.sessionKey === "string" ? params.sessionKey.trim() : "";
  if (sessionKey) {
    return sessionKey;
  }
  const sessionId = typeof params.sessionId === "string" ? params.sessionId.trim() : "";
  return sessionId || undefined;
}

export function shouldBypassSession(
  params: {
    sessionId?: string;
    sessionKey?: string;
  },
  patterns: RegExp[],
): boolean {
  if (patterns.length === 0) {
    return false;
  }
  const candidate = resolveSessionPatternCandidate(params);
  if (!candidate) {
    return false;
  }
  return matchesSessionPattern(candidate, patterns);
}

function normalizeDedupeText(text: string): string {
  return text.toLowerCase().replace(/\s+/g, " ").trim();
}

function normalizeCaptureDedupeText(text: string): string {
  return normalizeDedupeText(text).replace(/[\p{P}\p{S}]+/gu, " ").replace(/\s+/g, " ").trim();
}

export function pickRecentUniqueTexts(texts: string[], limit: number): string[] {
  if (limit <= 0 || texts.length === 0) {
    return [];
  }
  const seen = new Set<string>();
  const picked: string[] = [];
  for (let i = texts.length - 1; i >= 0; i -= 1) {
    const text = texts[i];
    const key = normalizeCaptureDedupeText(text);
    if (!key || seen.has(key)) {
      continue;
    }
    seen.add(key);
    picked.push(text);
    if (picked.length >= limit) {
      break;
    }
  }
  return picked.reverse();
}

export function getCaptureDecision(text: string, mode: CaptureMode, captureMaxLength: number): {
  shouldCapture: boolean;
  reason: string;
  normalizedText: string;
} {
  const trimmed = text.trim();
  const normalizedText = sanitizeUserTextForCapture(trimmed);
  const hadSanitization = normalizedText !== trimmed;
  if (!normalizedText) {
    return {
      shouldCapture: false,
      reason: /<relevant-memories>/i.test(trimmed) ? "injected_memory_context_only" : "empty_text",
      normalizedText: "",
    };
  }

  const compactText = normalizedText.replace(/\s+/g, "");
  const minLength = resolveCaptureMinLength(compactText);
  if (compactText.length < minLength || normalizedText.length > captureMaxLength) {
    return {
      shouldCapture: false,
      reason: "length_out_of_range",
      normalizedText,
    };
  }

  if (COMMAND_TEXT_RE.test(normalizedText)) {
    return {
      shouldCapture: false,
      reason: "command_text",
      normalizedText,
    };
  }

  if (NON_CONTENT_TEXT_RE.test(normalizedText)) {
    return {
      shouldCapture: false,
      reason: "non_content_text",
      normalizedText,
    };
  }
  if (SUBAGENT_CONTEXT_RE.test(normalizedText)) {
    return {
      shouldCapture: false,
      reason: "subagent_context",
      normalizedText,
    };
  }
  if (looksLikeQuestionOnlyText(normalizedText)) {
    return {
      shouldCapture: false,
      reason: "question_text",
      normalizedText,
    };
  }

  if (mode === "keyword") {
    for (const trigger of MEMORY_TRIGGERS) {
      if (trigger.test(normalizedText)) {
        return {
          shouldCapture: true,
          reason: hadSanitization
            ? `matched_trigger_after_sanitize:${trigger.toString()}`
            : `matched_trigger:${trigger.toString()}`,
          normalizedText,
        };
      }
    }
    return {
      shouldCapture: false,
      reason: hadSanitization ? "no_trigger_matched_after_sanitize" : "no_trigger_matched",
      normalizedText,
    };
  }

  return {
    shouldCapture: true,
    reason: hadSanitization ? "semantic_candidate_after_sanitize" : "semantic_candidate",
    normalizedText,
  };
}

export function extractTextsFromUserMessages(messages: unknown[]): string[] {
  const texts: string[] = [];
  for (const msg of messages) {
    if (!msg || typeof msg !== "object") {
      continue;
    }
    const msgObj = msg as Record<string, unknown>;
    if (msgObj.role !== "user") {
      continue;
    }
    const content = msgObj.content;
    if (typeof content === "string") {
      texts.push(content);
      continue;
    }
    if (Array.isArray(content)) {
      for (const block of content) {
        if (!block || typeof block !== "object") {
          continue;
        }
        const blockObj = block as Record<string, unknown>;
        if (blockObj.type === "text" && typeof blockObj.text === "string") {
          texts.push(blockObj.text);
        }
      }
    }
  }
  return texts;
}

function formatToolUseBlock(b: Record<string, unknown>): string {
  const name = typeof b.name === "string" ? b.name : "unknown";
  let inputStr = "";
  if (b.input !== undefined && b.input !== null) {
    try {
      inputStr = typeof b.input === "string" ? b.input : JSON.stringify(b.input);
    } catch {
      inputStr = String(b.input);
    }
  }
  return inputStr
    ? `[toolUse: ${name}]\n${inputStr}`
    : `[toolUse: ${name}]`;
}

function formatToolResultContent(content: unknown): string {
  if (typeof content === "string") return content.trim();
  if (Array.isArray(content)) {
    const parts: string[] = [];
    for (const block of content) {
      const b = block as Record<string, unknown>;
      if (b?.type === "text" && typeof b.text === "string") {
        parts.push((b.text as string).trim());
      }
    }
    return parts.join("\n");
  }
  if (content !== undefined && content !== null) {
    try {
      return JSON.stringify(content);
    } catch {
      return String(content);
    }
  }
  return "";
}

/**
 * Extract text from a single message without a `[role]:` prefix.
 * Used by afterTurn to send messages with their actual role.
 */
export function extractSingleMessageText(msg: unknown): string {
  if (!msg || typeof msg !== "object") return "";
  const m = msg as Record<string, unknown>;
  const role = m.role as string;
  if (!role || role === "system") return "";

  if (role === "toolResult") {
    const toolName = typeof m.toolName === "string" ? m.toolName : "tool";
    const resultText = formatToolResultContent(m.content);
    return resultText ? `[${toolName} result]: ${resultText}` : "";
  }

  const content = m.content;
  if (typeof content === "string") return content.trim();
  if (Array.isArray(content)) {
    const parts: string[] = [];
    for (const block of content) {
      const b = block as Record<string, unknown>;
      if (b?.type === "text" && typeof b.text === "string") {
        parts.push((b.text as string).trim());
      } else if (b?.type === "toolUse") {
        parts.push(formatToolUseBlock(b));
      }
    }
    return parts.join("\n");
  }
  return "";
}

function extractPartText(content: unknown): string {
  if (typeof content === "string") {
    return content.trim();
  }
  if (Array.isArray(content)) {
    const parts: string[] = [];
    for (const block of content) {
      const b = block as Record<string, unknown>;
      if (b?.type === "text" && typeof b.text === "string") {
        parts.push((b.text as string).trim());
      }
    }
    return parts.join(" ");
  }
  return "";
}

/**
 * 结构化消息类型 - 用于 afterTurn 发送到 OpenViking
 */
export type ExtractedMessage = {
  role: "user" | "assistant";
  parts: Array<{
    type: "text";
    text: string;
  } | {
    type: "tool";
    toolCallId?: string;
    toolName: string;
    toolInput?: Record<string, unknown>;
    toolOutput: string;
    toolStatus: string;
  }>;
};

type ToolResultSnapshot = {
  toolName: string;
  output: string;
};

function extractToolCallId(value: Record<string, unknown>): string {
  return String(value.toolCallId ?? value.toolUseId ?? value.tool_call_id ?? value.id ?? "");
}

function extractToolName(value: Record<string, unknown>, fallback = "tool"): string {
  return String(value.toolName ?? value.name ?? value.tool_name ?? fallback);
}

function extractToolInput(value: Record<string, unknown>): Record<string, unknown> | undefined {
  const input = value.arguments ?? value.input ?? value.toolInput ?? value.tool_input;
  return input && typeof input === "object" ? input as Record<string, unknown> : undefined;
}

function isToolUseBlock(value: Record<string, unknown>): boolean {
  return value.type === "toolCall" || value.type === "toolUse" || value.type === "tool_call";
}

function appendExtractedMessage(
  messages: ExtractedMessage[],
  role: "user" | "assistant",
  parts: ExtractedMessage["parts"],
  forceNew = false,
): void {
  if (parts.length === 0) {
    return;
  }
  const last = messages[messages.length - 1];
  if (!forceNew && last && last.role === role) {
    last.parts.push(...parts);
    return;
  }
  messages.push({ role, parts });
}

/**
 * 提取从 startIndex 开始的新消息，返回结构化消息。
 * - 用户输入 → type: "text"
 * - 工具结果 → type: "tool"
 * - 跳过 system 消息
 * - 清理时间戳前缀（如 [Fri 2026-04-10 17:20 GMT+8]）
 */
export function extractNewTurnMessages(
  messages: unknown[],
  startIndex: number,
): { messages: ExtractedMessage[]; newCount: number } {
  const result: ExtractedMessage[] = [];
  let count = 0;

  // First pass: collect tool results so assistant toolUse blocks can carry
  // their matching result when the pair is captured in the same afterTurn.
  const toolResultsById = new Map<string, ToolResultSnapshot>();
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i] as Record<string, unknown>;
    if (!msg || typeof msg !== "object") continue;
    const role = msg.role as string;
    if (role === "toolResult") {
      const toolCallId = extractToolCallId(msg);
      const output = formatToolResultContent(msg.content);
      if (toolCallId && output) {
        const toolName = extractToolName(msg);
        toolResultsById.set(toolCallId, { toolName, output });
      }
    }
  }

  const attachedToolResultIds = new Set<string>();
  let shouldSeparateNextMessage = false;

  for (let i = startIndex; i < messages.length; i++) {
    const msg = messages[i] as Record<string, unknown>;
    if (!msg || typeof msg !== "object") continue;

    const role = msg.role as string;
    if (!role || role === "system") continue;

    count++;

    if (role === "assistant" && Array.isArray(msg.content)) {
      const parts: ExtractedMessage["parts"] = [];
      for (const block of msg.content) {
        const b = block as Record<string, unknown>;
        if (b?.type === "text" && typeof b.text === "string") {
          const text = b.text.trim();
          if (text && !HEARTBEAT_RE.test(text)) {
            parts.push({ type: "text", text });
          }
          continue;
        }
        if (!isToolUseBlock(b)) {
          continue;
        }

        const toolCallId = extractToolCallId(b);
        const matchedResult = toolCallId ? toolResultsById.get(toolCallId) : undefined;
        if (toolCallId && matchedResult) {
          attachedToolResultIds.add(toolCallId);
        }
        const toolName = extractToolName(b, matchedResult?.toolName ?? "tool");
        parts.push({
          type: "tool",
          toolCallId: toolCallId || undefined,
          toolName,
          toolInput: extractToolInput(b),
          toolOutput: matchedResult ? `[${toolName} result]: ${matchedResult.output}` : "",
          toolStatus: matchedResult ? "completed" : "running",
        });
      }
      appendExtractedMessage(result, "assistant", parts, shouldSeparateNextMessage);
      shouldSeparateNextMessage = false;
      continue;
    }

    // Orphan toolResult -> user text. Matching assistant toolUse pairs are
    // already attached to their assistant message above.
    if (role === "toolResult") {
      const toolName = extractToolName(msg);
      const output = formatToolResultContent(msg.content);
      const toolCallId = extractToolCallId(msg);
      if (toolCallId && attachedToolResultIds.has(toolCallId)) {
        shouldSeparateNextMessage = true;
        continue;
      }
      if (output) {
        appendExtractedMessage(result, "user", [{
          type: "text",
          text: `[${toolName} result]: ${output}`,
        }]);
      }
      continue;
    }

    // user/assistant -> type: "text"
    // 保留原始 user/assistant 角色，并合并相邻同角色片段
    const content = msg.content;
    const text = extractPartText(content);

    if (text) {
      if (HEARTBEAT_RE.test(text)) {
        continue;
      }
      // 保持原始 role，assistant 保持 assistant，user 保持 user
      const ovRole: "user" | "assistant" = role === "assistant" ? "assistant" : "user";
      const cleanedText = ovRole === "user"
        ? (
          // 使用 sanitizeUserTextForCapture 清理所有噪音（Sender 元数据、时间戳等）
          sanitizeUserTextForCapture(text)
        )
        : text.trim();
      if (cleanedText) {
        appendExtractedMessage(result, ovRole, [{
          type: "text",
          text: cleanedText,
        }], shouldSeparateNextMessage);
        shouldSeparateNextMessage = false;
      }
    }
  }

  return { messages: result, newCount: count };
}

export function extractNewTurnTexts(
  messages: unknown[],
  startIndex: number,
): { texts: string[]; newCount: number } {
  const texts: string[] = [];
  let count = 0;
  for (let i = startIndex; i < messages.length; i++) {
    const msg = messages[i] as Record<string, unknown>;
    if (!msg || typeof msg !== "object") {
      continue;
    }
    const role = msg.role as string;
    if (!role || role === "system") {
      continue;
    }
    count++;

    const text = extractSingleMessageText(msg);
    if (!text) {
      continue;
    }
    // Mirror extractNewTurnMessages: skip heartbeat content so callers never
    // see synthetic keep-alive turns as real text.
    if (HEARTBEAT_RE.test(text)) {
      continue;
    }
    if (role === "toolResult") {
      texts.push(text);
    } else {
      texts.push(`[${role}]: ${text}`);
    }
  }
  return { texts, newCount: count };
}

export function extractLatestUserText(messages: unknown[] | undefined): string {
  if (!messages || messages.length === 0) {
    return "";
  }
  const texts = extractTextsFromUserMessages(messages);
  for (let i = texts.length - 1; i >= 0; i -= 1) {
    const normalized = sanitizeUserTextForCapture(texts[i] ?? "");
    if (normalized) {
      return normalized;
    }
  }
  return "";
}
