const TEXT_BLOCK_TYPES = new Set(["text", "input_text", "output_text"]);
const TOOL_CALL_TYPES = new Set([
  "tool_call",
  "toolcall",
  "tool_use",
  "tooluse",
  "function_call",
  "functioncall",
]);
const TOOL_RESULT_TYPES = new Set([
  "tool_result",
  "toolresult",
  "tool_output",
  "tooloutput",
  "function_call_output",
  "functioncalloutput",
]);

const ACK_RE = /^(?:ok|okay|k|yes|yep|no|nope|thanks|thank you|thx|done|收到|好的|好|嗯|可以|继续|不用|不需要|没了|好了)[.!?。！？\s]*$/i;
const SLASH_COMMAND_RE = /^\/[a-z0-9_-]{1,64}\b/i;
const METADATA_KEYS = [
  "session_id",
  "sessionid",
  "sessionkey",
  "conversation_id",
  "conversationid",
  "channel",
  "sender",
  "user_id",
  "userid",
  "agent_id",
  "agentid",
  "timestamp",
  "timezone",
  "cwd",
  "model",
  "permission_mode",
];

function normalizeType(value) {
  return String(value || "").toLowerCase().replace(/[-\s]/g, "_");
}

function isToolCallBlock(block) {
  const type = normalizeType(block?.type || block?.kind || block?.role);
  return TOOL_CALL_TYPES.has(type) || Boolean(block?.tool_calls) || Boolean(block?.function?.name);
}

function isToolResultBlock(block) {
  const type = normalizeType(block?.type || block?.kind || block?.role);
  return TOOL_RESULT_TYPES.has(type) || type === "tool" || type === "function";
}

function oneLine(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

export function truncateCaptureText(text, maxChars = 2000) {
  const value = String(text || "").trim();
  if (!Number.isFinite(maxChars) || maxChars <= 0 || value.length <= maxChars) return value;
  return `${value.slice(0, Math.max(0, maxChars - 20)).trimEnd()}\n[truncated]`;
}

function stringifyCompact(value, maxChars) {
  if (value == null) return "";
  if (typeof value === "string") return truncateCaptureText(value, maxChars);
  try {
    return truncateCaptureText(JSON.stringify(value), maxChars);
  } catch {
    return truncateCaptureText(String(value), maxChars);
  }
}

function parseMaybeJson(value) {
  if (value == null || typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed) return value;
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function blockText(block) {
  if (!block || typeof block !== "object") return "";
  if (typeof block.text === "string") return block.text;
  if (typeof block.output_text === "string") return block.output_text;
  if (typeof block.input_text === "string") return block.input_text;
  if (typeof block.content === "string") return block.content;
  return "";
}

function toolName(block) {
  return oneLine(
    block?.name ||
    block?.tool_name ||
    block?.toolName ||
    block?.function?.name ||
    block?.call?.name ||
    block?.id ||
    "",
  );
}

function toolPayload(block, kind) {
  if (!block || typeof block !== "object") return "";
  if (kind === "call") {
    return block.input ??
      block.arguments ??
      block.args ??
      block.params ??
      block.function?.arguments ??
      block.command ??
      block.call?.input ??
      block.call?.arguments ??
      "";
  }
  return block.output ??
    block.result ??
    block.error ??
    block.data ??
    block.content ??
    block.text ??
    "";
}

function toolId(block) {
  return oneLine(
    block?.id ||
    block?.call_id ||
    block?.callId ||
    block?.tool_call_id ||
    block?.toolCallId ||
    block?.tool_use_id ||
    block?.toolUseId ||
    block?.function_call_id ||
    block?.functionCallId ||
    "",
  );
}

function toolStatus(block, kind) {
  if (kind === "call") return "running";
  if (block?.is_error || block?.error) return "error";
  const status = oneLine(block?.status || "");
  return status || "completed";
}

function buildToolPart(block, kind, { toolMaxChars = 2000, toolNameById = {} } = {}) {
  const id = toolId(block);
  const name = toolName(block) || (id ? toolNameById[id] : "");
  const payload = toolPayload(block, kind);
  const part = {
    type: "tool",
    tool_id: id || undefined,
    tool_name: name || undefined,
    tool_status: toolStatus(block, kind),
  };
  if (kind === "call") {
    const input = parseMaybeJson(payload);
    if (input !== "" && input != null) {
      part.tool_input = typeof input === "object" && !Array.isArray(input)
        ? input
        : { value: input };
    }
  } else {
    part.tool_output = stringifyCompact(payload, toolMaxChars);
  }
  return part;
}

function formatToolBlock(block, kind, maxChars) {
  const name = toolName(block);
  const payload = toolPayload(block, kind);
  const body = oneLine(stringifyCompact(payload, maxChars));
  const label = kind === "call" ? "tool-call" : "tool-result";
  return body
    ? `[${label}${name ? ` ${name}` : ""}] ${body}`
    : `[${label}${name ? ` ${name}` : ""}]`;
}

function blockToText(block, options) {
  if (!block) return "";
  if (typeof block === "string") return block;
  if (Array.isArray(block)) return extractTextFromContent(block, options);
  if (typeof block !== "object") return "";

  if (block.item && typeof block.item === "object") {
    const itemText = blockToText(block.item, options);
    if (itemText) return itemText;
  }

  const type = normalizeType(block.type || block.kind || block.role);
  if (TEXT_BLOCK_TYPES.has(type)) return blockText(block);
  if (isToolCallBlock(block)) return formatToolBlock(block, "call", options.toolMaxChars);
  if (isToolResultBlock(block)) return formatToolBlock(block, "result", options.toolMaxChars);
  if (Array.isArray(block.content)) return extractTextFromContent(block.content, options);
  if (!type) return blockText(block);
  return "";
}

export function extractTextFromContent(content, options = {}) {
  const opts = { toolMaxChars: 2000, ...options };
  if (!content) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => blockToText(block, opts))
      .filter(Boolean)
      .join("\n\n");
  }
  if (typeof content === "object") {
    return blockToText(content, opts) || stringifyCompact(content, opts.toolMaxChars);
  }
  return "";
}

export function extractTextFromPayload(payload, options = {}) {
  if (!payload || typeof payload !== "object") return "";
  const chunks = [];
  const directType = normalizeType(payload.type || payload.kind || payload.role);
  if (TOOL_RESULT_TYPES.has(directType) || directType === "tool" || TOOL_CALL_TYPES.has(directType)) {
    const direct = blockToText(payload, { toolMaxChars: 2000, ...options });
    if (direct) return direct;
  }

  if (payload.message && typeof payload.message === "object") {
    const messageText = extractTextFromPayload(payload.message, options);
    if (messageText) chunks.push(messageText);
  } else if (payload.content !== undefined) {
    const contentText = extractTextFromContent(payload.content, options);
    if (contentText) chunks.push(contentText);
  }

  for (const key of ["tool_calls", "toolCalls", "function_call", "functionCall", "tool_call", "toolCall"]) {
    const value = payload[key];
    if (!value) continue;
    const toolText = extractTextFromContent(value, options);
    if (toolText) chunks.push(toolText);
  }

  if (chunks.length === 0) {
    const direct = blockToText(payload, { toolMaxChars: 2000, ...options });
    if (direct) chunks.push(direct);
  }

  return chunks.join("\n\n");
}

function collectToolNamesByIdFromEntries(entries) {
  const map = {};
  for (const entry of entries || []) {
    const payload = entry?.payload && typeof entry.payload === "object" ? entry.payload : entry;
    collectToolNamesByIdFromPayload(payload, map);
  }
  return map;
}

function collectToolNamesByIdFromPayload(payload, out) {
  if (!payload || typeof payload !== "object") return;
  if (payload.message && typeof payload.message === "object") {
    collectToolNamesByIdFromPayload(payload.message, out);
  }
  const candidates = [];
  if (Array.isArray(payload.content)) candidates.push(...payload.content);
  for (const key of ["tool_calls", "toolCalls", "function_call", "functionCall", "tool_call", "toolCall"]) {
    const value = payload[key];
    if (!value) continue;
    if (Array.isArray(value)) candidates.push(...value);
    else candidates.push(value);
  }
  if (isToolCallBlock(payload)) candidates.push(payload);
  for (const block of candidates) {
    if (!block || typeof block !== "object") continue;
    if (!isToolCallBlock(block)) continue;
    const id = toolId(block);
    const name = toolName(block);
    if (id && name) out[id] = name;
  }
}

function extractPartsFromContent(content, options = {}) {
  const opts = { toolMaxChars: 2000, toolNameById: {}, ...options };
  const parts = [];
  if (!content) return parts;
  if (typeof content === "string") {
    if (content.trim()) parts.push({ type: "text", text: content });
    return parts;
  }
  if (!Array.isArray(content)) {
    const text = blockToText(content, opts);
    if (text.trim()) parts.push({ type: "text", text });
    return parts;
  }
  for (const block of content) {
    if (!block || typeof block !== "object") continue;
    if (isToolCallBlock(block)) {
      parts.push(buildToolPart(block, "call", opts));
    } else if (isToolResultBlock(block)) {
      parts.push(buildToolPart(block, "result", opts));
    } else {
      const text = blockToText(block, opts);
      if (text.trim()) parts.push({ type: "text", text });
    }
  }
  return parts;
}

export function extractPartsFromPayload(payload, options = {}) {
  if (!payload || typeof payload !== "object") return [];
  const opts = { toolMaxChars: 2000, toolNameById: {}, ...options };
  if (payload.message && typeof payload.message === "object") {
    return extractPartsFromPayload(payload.message, opts);
  }

  const directType = normalizeType(payload.type || payload.kind || payload.role);
  if (TOOL_CALL_TYPES.has(directType) || isToolCallBlock(payload)) {
    return [buildToolPart(payload, "call", opts)];
  }
  if (TOOL_RESULT_TYPES.has(directType) || directType === "tool" || directType === "function") {
    return [buildToolPart(payload, "result", opts)];
  }

  const parts = [];
  if (payload.content !== undefined) {
    parts.push(...extractPartsFromContent(payload.content, opts));
  }
  for (const key of ["tool_calls", "toolCalls", "function_call", "functionCall", "tool_call", "toolCall"]) {
    const value = payload[key];
    if (!value) continue;
    if (Array.isArray(value)) {
      for (const block of value) parts.push(...extractPartsFromPayload(block, opts));
    } else {
      parts.push(...extractPartsFromPayload(value, opts));
    }
  }
  return parts;
}

export function extractCaptureTurns(rolloutEntries, cfg = {}) {
  const toolNameById = collectToolNamesByIdFromEntries(rolloutEntries);
  const turns = [];
  for (const entry of rolloutEntries || []) {
    if (!entry || typeof entry !== "object") continue;
    const payload = entry.payload && typeof entry.payload === "object" ? entry.payload : entry;
    const message = payload.message && typeof payload.message === "object" ? payload.message : null;
    const rawRole = message?.role || payload.role || payload.type || payload.kind;
    const role = normalizeCaptureRole(rawRole);
    if (!role) continue;
    if (isAssistantSideCaptureRole(rawRole) && !cfg.captureAssistantTurns) continue;

    const rawText = extractTextFromPayload(payload, { toolMaxChars: cfg.captureToolMaxChars });
    const parts = extractPartsFromPayload(payload, {
      toolMaxChars: cfg.captureToolMaxChars,
      toolNameById,
    });
    const decision = shouldCaptureText(rawText, role, cfg);
    if (!decision.shouldCapture && parts.length === 0) continue;
    const text = decision.shouldCapture ? decision.text : "";
    turns.push({ role, text, parts });
  }
  return turns;
}

export function normalizeCaptureRole(role) {
  const value = normalizeType(role);
  if (value === "user") return "user";
  if (value === "assistant") return "assistant";
  if (value === "tool" || value === "tool_result" || value === "function" || value === "function_call_output") {
    return "user";
  }
  if (value === "tool_call" || value === "function_call") return "assistant";
  return null;
}

export function isAssistantSideCaptureRole(role) {
  const value = normalizeType(role);
  return value === "assistant" ||
    value === "tool" ||
    value === "tool_result" ||
    value === "tool_call" ||
    value === "function" ||
    value === "function_call" ||
    value === "function_call_output";
}

function stripMetadataFences(text) {
  return String(text || "").replace(/```(?:json)?\s*([\s\S]*?)```/gi, (match, body) => {
    const lower = body.toLowerCase();
    let hits = 0;
    for (const key of METADATA_KEYS) {
      const re = new RegExp(`["']?${key}["']?\\s*:`, "i");
      if (re.test(lower)) hits += 1;
    }
    return hits >= 3 ? "" : match;
  });
}

function stripInjectedDigestBlocks(text) {
  const lines = String(text || "").split(/\r?\n/);
  const out = [];
  let skipping = false;
  let skipUntilMcpHint = false;

  for (const line of lines) {
    const trimmed = line.trim();
    if (/^OpenViking session archive digest:/i.test(trimmed)) {
      skipping = true;
      skipUntilMcpHint = true;
      continue;
    }
    if (/^OpenViking memory digest:/i.test(trimmed)) {
      skipping = true;
      skipUntilMcpHint = false;
      continue;
    }
    if (skipping) {
      if (skipUntilMcpHint) {
        if (/^More detail: use the OpenViking MCP /i.test(trimmed)) {
          skipping = false;
          skipUntilMcpHint = false;
        }
        continue;
      }
      if (!trimmed) {
        skipping = false;
        continue;
      }
      if (
        /^(?:[-*]\s+|#{1,6}\s+|More detail:|Use OpenViking MCP|Latest committed archive|Resume continuity|viking:\/\/)/i.test(trimmed) ||
        /^\s{2,}\S/.test(line)
      ) {
        continue;
      }
      skipping = false;
    }
    out.push(line);
  }

  return out.join("\n");
}

export function sanitizeCapturedText(text) {
  let value = String(text || "");
  value = value
    .replace(/\u0000/g, "")
    .replace(/<openviking-context\b[^>]*>[\s\S]*?<\/openviking-context>/gi, " ")
    .replace(/<relevant-memor(?:y|ies)\b[^>]*>[\s\S]*?<\/relevant-memor(?:y|ies)>/gi, " ")
    .replace(/^\s*Sender\s*\([^)]+\)\s*```[\s\S]*?```\s*/gim, " ")
    .replace(/^\s*Conversation (?:metadata|info):\s*```[\s\S]*?```\s*/gim, " ")
    .replace(/^\s*\[?\d{4}-\d{2}-\d{2}[T ][^\]\n]{3,80}\]?\s*/gm, "")
    .replace(/^\s*\d{10,13}\s+/gm, "");
  value = stripMetadataFences(value);
  value = stripInjectedDigestBlocks(value);
  value = value.replace(/^\s*More detail: use the OpenViking MCP .*$/gim, " ");
  return value
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function hasEnoughSignal(text) {
  const cjk = text.match(/[\u3400-\u9fff]/g)?.length || 0;
  const alnum = text.match(/[a-z0-9]/gi)?.length || 0;
  return cjk >= 4 || alnum >= 6 || text.length >= 12;
}

function isPunctuationOnly(text) {
  return !/[a-z0-9\u3400-\u9fff]/i.test(text);
}

export function shouldCaptureText(text, role, cfg = {}) {
  const maxLength = cfg.captureMaxLength || 24000;
  const sanitized = sanitizeCapturedText(text);
  if (!sanitized) return { shouldCapture: false, reason: "empty", text: "" };

  const capped = truncateCaptureText(sanitized, maxLength);
  const compact = oneLine(capped);
  const isToolSummary = /^\[tool-(?:call|result)\b/i.test(compact);

  if (!isToolSummary && role === "user" && SLASH_COMMAND_RE.test(compact)) {
    return { shouldCapture: false, reason: "slash_command", text: "" };
  }
  if (!isToolSummary && ACK_RE.test(compact)) {
    return { shouldCapture: false, reason: "ack", text: "" };
  }
  if (!isToolSummary && isPunctuationOnly(compact)) {
    return { shouldCapture: false, reason: "punctuation", text: "" };
  }
  if (!isToolSummary && !hasEnoughSignal(compact)) {
    return { shouldCapture: false, reason: "too_short", text: "" };
  }
  if (/^\[openviking-memory\]/i.test(compact)) {
    return { shouldCapture: false, reason: "plugin_status", text: "" };
  }

  return { shouldCapture: true, reason: "ok", text: capped };
}
