/**
 * Claude Code transcript reading, on top of the shared capture utilities.
 *
 * Claude writes JSONL of Anthropic messages, whose blocks differ from the shape
 * `capture-utils` walks in one way that matters: a `tool_result` nests its
 * output in a content array of text blocks and names the call it answers by id
 * rather than by name. Normalising that is all this harness needs of its own —
 * the block walking, the part building and the capture filter are shared.
 */

import {
  collectToolNamesByIdFromEntries,
  extractPartsFromPayload,
  normalizeCaptureRole,
} from "./shared/capture-utils.mjs";

// Named one by one rather than re-exported wholesale: this module has an
// `extractCaptureTurns` of its own, and `export *` would let the shared one
// through under the same name with nothing to say which a caller holds.
export { sanitizeCapturedText } from "./shared/capture-utils.mjs";

// Tool output retention in the per-turn *text*. 0 = drop it: the extraction
// signal lives in the agent's prose about what happened, not in the raw bytes a
// tool returned (file contents, web pages, command stdout), and the output
// still travels verbatim in the turn's tool part. Operators who want
// replay-style archives can set this above zero.
const TOOL_RESULT_MAX_CHARS = 0;

/** Claude's transcript is JSONL; older ones are a single JSON array. */
export function parseTranscript(content) {
  try {
    const data = JSON.parse(content);
    if (Array.isArray(data)) return data;
  } catch { /* not a JSON array */ }

  const messages = [];
  for (const line of String(content || "").split("\n")) {
    if (!line.trim()) continue;
    try { messages.push(JSON.parse(line)); } catch { /* skip */ }
  }
  return messages;
}

function extractToolResultText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((block) => block && block.type === "text" && typeof block.text === "string")
    .map((block) => block.text)
    .join("\n");
}

// Results land in a separable tool_output field and are reported verbatim — the
// server externalizes anything oversized and leaves a stub plus tool_output_ref,
// so this cap only guards against pathological payloads.
function truncateToolOutput(text, cfg) {
  const value = typeof text === "string" ? text : String(text ?? "");
  const max = cfg.captureToolMaxChars;
  if (!(max > 0) || value.length <= max) return value;
  return `${value.slice(0, max)}\n... [truncated, ${value.length - max} more chars]`;
}

function truncateToolResult(text) {
  if (TOOL_RESULT_MAX_CHARS <= 0) return null;
  const value = typeof text === "string" ? text : String(text ?? "");
  if (value.length <= TOOL_RESULT_MAX_CHARS) return value;
  return `${value.slice(0, TOOL_RESULT_MAX_CHARS)}\n... [truncated, ${value.length - TOOL_RESULT_MAX_CHARS} more chars]`;
}

// Tool inputs are agent-authored. They stay verbatim — usually short (URLs,
// paths, queries), and a pathologically long one is itself signal.
function formatToolInput(value) {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function normalizeBlock(block, cfg) {
  if (!block || typeof block !== "object" || block.type !== "tool_result") return block;
  return {
    type: "tool_result",
    tool_use_id: block.tool_use_id,
    output: truncateToolOutput(extractToolResultText(block.content), cfg),
    is_error: Boolean(block.is_error),
  };
}

function normalizeMessage(message, cfg, includeQueued) {
  if (!message || typeof message !== "object") return null;
  const attachment = message.attachment;
  if (includeQueued && message.type === "attachment" && attachment?.type === "queued_command") {
    // SDK input omits the interactive humanTurn/origin fields. Explicit agent
    // notifications are not human input; capture the raw prompt, never rendered UI.
    if (attachment.commandMode !== "prompt" || typeof attachment.prompt !== "string"
      || attachment.humanTurn === false
      || (attachment.origin?.kind && attachment.origin.kind !== "human")) return null;
    return { role: "user", content: attachment.prompt };
  }
  let role = message.role;
  let content;
  if (message.content !== undefined) {
    content = message.content;
  } else if (message.message && typeof message.message === "object") {
    // Claude Code also wraps copies of messages in bookkeeping lines (e.g. an
    // `api-request-blob` repeats a request message already in the transcript);
    // only the user/assistant entries are the conversation.
    if (message.type !== undefined && message.type !== "user" && message.type !== "assistant") return null;
    role = message.message.role || role;
    content = message.message.content;
  }
  return {
    role,
    content: Array.isArray(content)
      ? content.map((block) => normalizeBlock(block, cfg))
      : content,
  };
}

// The text the capture heuristics read: tool calls inlined with their input,
// tool results per TOOL_RESULT_MAX_CHARS.
function textFromParts(parts) {
  const chunks = [];
  for (const part of parts) {
    if (part.type !== "tool") {
      chunks.push(part.text);
    } else if (part.tool_status === "running") {
      chunks.push(`[tool: ${part.tool_name || "unknown"}]\n${formatToolInput(part.tool_input)}`);
    } else {
      const retained = truncateToolResult(part.tool_output);
      if (retained) chunks.push(`[tool result]\n${retained}`);
    }
  }
  return chunks.join("\n\n").trim();
}

function extractCaptureEntries(messages, cfg, includeQueued) {
  const payloads = (messages || []).map((message) => normalizeMessage(message, cfg, includeQueued));
  const toolNameById = collectToolNamesByIdFromEntries(payloads.filter(Boolean));
  const entries = [];
  const userIds = new Set();
  const queuedIds = new Set();
  for (const [index, payload] of payloads.entries()) {
    const role = payload && normalizeCaptureRole(payload.role);
    if (!role) continue;
    // Output was already capped by normalizeBlock; do not cap it again here.
    const parts = extractPartsFromPayload(payload, { toolMaxChars: Infinity, toolNameById });
    if (parts.length === 0) continue;
    const message = messages[index];
    const queued = includeQueued && message.type === "attachment";
    const ids = queued ? [message.attachment.source_uuid, message.attachment.delivery_id]
      .filter((id) => typeof id === "string" && id.length > 0) : [];
    // Keep the first representation in source order. A later normal user row
    // can name the same queued event; equal text with different IDs stays distinct.
    if (queued && ids.some((id) => userIds.has(id) || queuedIds.has(id))) continue;
    if (!queued && role === "user" && queuedIds.has(message.uuid)) continue;
    if (queued) ids.forEach((id) => queuedIds.add(id));
    else if (role === "user" && message.uuid) userIds.add(message.uuid);
    entries.push({ index, turn: { role, text: textFromParts(parts), parts } });
  }
  return entries;
}

/**
 * Every user/assistant turn in the transcript, in order.
 *
 * Turns carrying no structured part are dropped; the rest keep their index, so
 * a caller can treat the position of a turn as a durable cursor.
 */
export function extractCaptureTurns(messages, cfg = {}) {
  return extractCaptureEntries(messages, cfg, true).map((entry) => entry.turn);
}

export const CAPTURE_CURSOR_VERSION = 2;

/** Map an old turn-count boundary without replaying already captured legacy turns. */
export function mapLegacyCaptureCursor(messages, cfg, count) {
  if (!Number.isSafeInteger(count) || count < 0) throw new Error("Invalid capture cursor");
  if (count === 0) return 0;
  const legacy = extractCaptureEntries(messages, cfg, false);
  if (count > legacy.length) throw new Error("Capture cursor exceeds legacy transcript");
  const boundary = legacy[count - 1].index;
  return extractCaptureEntries(messages, cfg, true).filter((entry) => entry.index <= boundary).length;
}
