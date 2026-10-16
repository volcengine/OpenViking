/**
 * Pure text/token helpers shared by the context-window core and the sync
 * manager. Extracted verbatim from the takeover core of
 * examples/pi-coding-agent-extension so this extension carries no takeover
 * state machine.
 */

/**
 * Every user message this extension synthesizes (recall block, window header)
 * opens with this marker, so turn counting can tell an injected message apart
 * from something the human typed.
 */
export const CONTEXT_BLOCK_MARKER = "<openviking-context";

function flattenValue(value) {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(flattenValue).filter(Boolean).join("");
  if (!value || typeof value !== "object") return "";
  if (typeof value.text === "string") return value.text;
  if (typeof value.input_text === "string") return value.input_text;
  if (typeof value.output_text === "string") return value.output_text;
  if (typeof value.content === "string") return value.content;
  if (Array.isArray(value.content)) return flattenValue(value.content);
  return "";
}

export function flattenContent(msg) {
  if (!msg || typeof msg !== "object") return "";
  return flattenValue(msg.content);
}

export function fingerprintMessage(msg) {
  const text = flattenContent(msg);
  return `${msg?.role || ""}:${text.length}:${text.slice(0, 200)}`;
}

export function isUserTurnStart(msg) {
  if (!msg || msg.role !== "user") return false;
  return !flattenContent(msg).startsWith(CONTEXT_BLOCK_MARKER);
}

export function countUserTurns(messages) {
  let count = 0;
  for (const msg of Array.isArray(messages) ? messages : []) {
    if (isUserTurnStart(msg)) count++;
  }
  return count;
}

export function estimateTokens(text) {
  const value = String(text || "");
  if (!value) return 0;
  let cjk = 0;
  let other = 0;
  for (const ch of value) {
    if (ch.codePointAt(0) >= 0x3000) cjk++;
    else other++;
  }
  return Math.ceil(cjk * 1.5 + other / 4);
}

export function truncateToTokens(text, budget) {
  const value = String(text || "");
  const limit = Math.max(0, Math.floor(Number(budget) || 0));
  if (!value || limit <= 0) return "";
  if (estimateTokens(value) <= limit) return value;

  let lo = 0;
  let hi = value.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (estimateTokens(value.slice(0, mid)) <= limit) lo = mid;
    else hi = mid - 1;
  }
  return value.slice(0, lo);
}

function partText(part) {
  if (!part || typeof part !== "object") return "";
  if (typeof part.text === "string") return part.text;
  if (part.type === "tool") {
    const payload = {
      name: part.tool_name,
      input: part.tool_input,
      output: part.tool_output,
      status: part.tool_status,
    };
    try {
      return JSON.stringify(payload);
    } catch {
      return String(part.tool_name || part.tool_output || "");
    }
  }
  return flattenValue(part);
}

export function estimatePayloadTokens(payload) {
  if (!payload || typeof payload !== "object") return 0;
  if (typeof payload.content === "string") return estimateTokens(payload.content);
  if (Array.isArray(payload.parts)) {
    return estimateTokens(payload.parts.map(partText).filter(Boolean).join("\n\n"));
  }
  if (Array.isArray(payload.content)) {
    return estimateTokens(payload.content.map(partText).filter(Boolean).join("\n\n"));
  }
  return 0;
}
