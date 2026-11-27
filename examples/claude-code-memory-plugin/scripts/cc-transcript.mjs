/**
 * Claude Code transcript reading, on top of the shared capture utilities.
 *
 * Claude writes JSONL of Anthropic messages, whose blocks differ from the shape
 * `capture-utils` walks in one way that matters: a `tool_result` nests its
 * output in a content array of text blocks and names the call it answers by id
 * rather than by name. Normalising that is all this harness needs of its own —
 * the block walking and the building of the message sent are shared.
 */

import {
  buildCaptureMessage,
  collectToolNamesByIdFromEntries,
  extractPartsFromPayload,
  normalizeCaptureRole,
} from "./shared/capture-utils.mjs";

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

function normalizeBlock(block) {
  if (!block || typeof block !== "object" || block.type !== "tool_result") return block;
  return {
    type: "tool_result",
    tool_use_id: block.tool_use_id,
    output: extractToolResultText(block.content),
    is_error: Boolean(block.is_error),
  };
}

function normalizeMessage(message) {
  if (!message || typeof message !== "object") return null;
  let role = message.role;
  let content;
  let hostAuthored = false;
  if (message.content !== undefined) {
    content = message.content;
  } else if (message.message && typeof message.message === "object") {
    // Claude Code also wraps copies of messages in bookkeeping lines (e.g. an
    // `api-request-blob` repeats a request message already in the transcript);
    // only the user/assistant entries are the conversation.
    if (message.type !== undefined && message.type !== "user" && message.type !== "assistant") return null;
    // Host-authored records in the user's role: the compaction summary, and
    // meta records such as expanded skill bodies and local-command caveats.
    hostAuthored = message.isCompactSummary === true || message.isMeta === true;
    role = message.message.role || role;
    content = message.message.content;
  }
  return {
    role,
    content: Array.isArray(content)
      ? content.map(normalizeBlock)
      : content,
    hostAuthored,
  };
}

/**
 * Every user/assistant turn in the transcript, in order, each with the session
 * message it is sent as (`message`, null when nothing in it is to be captured).
 *
 * Turns carrying no structured part are dropped; the rest keep their index, so
 * a caller can treat the position of a turn as a durable cursor. Host-authored
 * records (the compaction summary, meta records) stay in the sequence, flagged
 * `hostAuthored`, so cursors written before they were recognised still line
 * up; callers leave them out of what they send.
 */
export function extractCaptureTurns(messages, cfg = {}) {
  const payloads = (messages || []).map(normalizeMessage);
  const toolNameById = collectToolNamesByIdFromEntries(payloads.filter(Boolean));
  const turns = [];
  for (const payload of payloads) {
    const role = payload && normalizeCaptureRole(payload.role);
    if (!role) continue;
    const parts = extractPartsFromPayload(payload, { toolNameById });
    if (parts.length === 0) continue;
    const message = buildCaptureMessage(payload, payload.role, cfg, { toolNameById });
    const turn = { role, parts, message };
    if (payload.hostAuthored) turn.hostAuthored = true;
    turns.push(turn);
  }
  return turns;
}
