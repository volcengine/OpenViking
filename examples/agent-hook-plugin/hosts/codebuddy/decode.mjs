/**
 * Decode CodeBuddy's transcript into stable incremental user/assistant turns.
 *
 * CodeBuddy writes JSONL where conversation and tool activity live in separate
 * records, and where the human ask is wrapped in host-injected markup
 * (DESIGN.md §8):
 *
 *   {"type":"message","role":"user"|"assistant","content":[{"type":"input_text"|"output_text"|"text","text":"…"}]}
 *   {"type":"function_call","name":"Bash","arguments":"…","callId":"call_…"}
 *   {"type":"function_call_result","name":"Bash","callId":"call_…","status":"completed"|"incomplete","output":{"type":"text","text":"…"}}
 *   {"type":"reasoning"|"file-history-snapshot"|"summary"|"turn-metrics"|…}
 *
 * 1. Tool records are re-typed (`function_call_result` → `tool_result`) and a
 *    deferred call (every MCP tool) through `DeferExecuteTool` is unwrapped, so
 *    the shared part builder sees a shape it understands.
 *
 * 2. When the host injects context it wraps the ask in `<user_query>` and
 *    surrounds it with host blocks, several of which quote earlier turns
 *    (`<previous_user_message>` wraps a whole `<user_query>`). The tag list and
 *    the "<user_query>, else longest candidate" rule mirror the server-side
 *    `workbuddy` ingest adapter (openviking/ingest/sources/workbuddy.py).
 *    Measured over real transcripts, most user records carry no `<user_query>`
 *    at all, so the host-block-stripped text is the fallback. Host-authored
 *    records are marked by `providerData.isMeta` (task notifications,
 *    "continue" nudges) or `providerData.skipRun` (slash commands, the command
 *    caveat) and dropped outright.
 *
 * A `message` record opens a turn; the tool records after an assistant message
 * fold into it. Tool activity with no assistant message before it forms an
 * assistant turn of its own. A turn's id is the index of the record that
 * opened it: the transcript is append-only.
 */

import { normalizeCaptureRole } from "../../lib/capture-utils.mjs";
import { parseJsonl, resumeTurns, turnsFromBlocks } from "../../lib/flow/turns.mjs";

const TEXT_BLOCK_TYPES = new Set(["text", "input_text", "output_text"]);

// Mirrors `_HOST_BLOCK_TAGS` in openviking/ingest/sources/workbuddy.py.
export const HOST_BLOCK_TAGS = [
  "system-reminder",
  "memory_and_skills_reminder",
  "automation_system_reminder",
  "previous_user_message",
  "previous_assistant_message",
  "previous_tool_call",
  "cb_summary",
  "conversation_history_summary",
  "additional_data",
  "user_info",
  "identity_context",
  "craft_mode",
  "connector-status",
];
const HOST_BLOCK_RE = new RegExp(
  `<(?<tag>${HOST_BLOCK_TAGS.join("|")})(?:\\s[^>]*)?>.*?</\\k<tag>\\s*>`,
  "gis",
);
const USER_QUERY_RE = /<user_query\s*>(.*?)<\/user_query\s*>/gis;

/** The human ask in a user record's text, or "" when there is none. */
function userTurnText(raw) {
  // Host blocks go first, so a quoted `<user_query>` is never the current ask.
  const stripped = String(raw || "").replace(HOST_BLOCK_RE, " ");
  const queries = [...stripped.matchAll(USER_QUERY_RE)].map((m) => m[1].trim()).filter(Boolean);
  if (queries.length === 0) return stripped.trim();
  return queries.reduce((a, b) => (b.length > a.length ? b : a));
}

function isHostAuthored(record) {
  const meta = record?.providerData;
  return Boolean(meta && (meta.isMeta === true || meta.skipRun === true));
}

function textsOf(content) {
  if (typeof content === "string") return content.trim() ? [content] : [];
  if (!Array.isArray(content)) return [];
  return content
    .filter((block) => block && TEXT_BLOCK_TYPES.has(String(block.type || "").toLowerCase()))
    .map((block) => block.text)
    .filter((text) => typeof text === "string" && text.trim());
}

// Deferred tools (MCP tools among them) are called through `DeferExecuteTool`
// with the real tool in `toolName`; the part names that tool.
function unwrapDeferredCall(record) {
  const plain = { name: record.name, arguments: record.arguments };
  if (record.name !== "DeferExecuteTool") return plain;
  let args = record.arguments;
  if (typeof args === "string") {
    try { args = JSON.parse(args); } catch { return plain; }
  }
  const toolName = args?.toolName;
  return typeof toolName === "string" && toolName ? { name: toolName, arguments: args.params ?? {} } : plain;
}

// A failed tool call is `incomplete`, but a failed shell command is
// `completed` and says so only in `providerData.toolResult.rawResponse`.
function isErrorResult(record) {
  return record.status === "incomplete" || record.providerData?.toolResult?.rawResponse?.is_error === true;
}

function decodeRecord(record, namesByCallId) {
  const type = String(record?.type || "");
  if (type === "message") {
    const role = normalizeCaptureRole(record.role);
    if (!role) return null;
    const texts = textsOf(record.content);
    if (role === "user") {
      if (isHostAuthored(record)) return null;
      const ask = userTurnText(texts.join("\n"));
      return ask ? { role, text: ask } : null;
    }
    return texts.length > 0 ? { role, text: texts.join("\n") } : null;
  }
  const callId = record?.callId || record?.call_id;
  if (type === "function_call") {
    const call = unwrapDeferredCall(record);
    if (callId) namesByCallId.set(callId, call.name);
    return { block: { type: "function_call", name: call.name, arguments: call.arguments, call_id: callId } };
  }
  // `in_progress` marks a streamed partial result; the finished one follows.
  if (type === "function_call_result" && record.status !== "in_progress") {
    const { output } = record;
    const block = {
      type: "tool_result",
      name: namesByCallId.get(callId) || record.name,
      // The output is a text block on the host; the part carries its text.
      output: output?.type === "text" && typeof output.text === "string" ? output.text : output,
      call_id: callId,
    };
    if (isErrorResult(record)) block.is_error = true;
    return { block };
  }
  return null;
}

export function extractCodebuddyTurns(records) {
  const collected = [];
  let current = null;
  const open = (role, index) => {
    current = { role, blocks: [], recordIndex: index };
    collected.push(current);
  };
  const namesByCallId = new Map();
  (records || []).forEach((record, index) => {
    const decoded = decodeRecord(record, namesByCallId);
    if (!decoded) return;
    if (decoded.role) {
      open(decoded.role, index);
      current.blocks.push({ type: "text", text: decoded.text });
    } else {
      if (current?.role !== "assistant") open("assistant", index);
      current.blocks.push(decoded.block);
    }
  });
  return turnsFromBlocks(collected);
}

/** @param {import("../../lib/flow/host.d.mts").DecodeInput} input */
export function decode({ text, cursor }) {
  return resumeTurns(extractCodebuddyTurns(parseJsonl(text)), cursor);
}
