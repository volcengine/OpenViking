/**
 * Decode CodeBuddy's transcript into stable incremental user/assistant turns.
 *
 * CodeBuddy writes JSONL where conversation and tool activity live in *separate
 * records*, and where the human ask is wrapped in host-injected markup:
 *
 *   {"type":"message","role":"user"|"assistant","content":[{"type":"input_text"|"output_text"|"text","text":"…"}]}
 *   {"type":"function_call","name":"Bash","arguments":"…","callId":"call_…"}
 *   {"type":"function_call_result","name":"Bash","callId":"call_…","status":"completed"|"incomplete","output":{"type":"text","text":"…"}}
 *   {"type":"reasoning"|"reasoning_text"|"file-history-snapshot"|"summary"|"turn-metrics"|…}
 *
 * Two things differ from what `capture-utils` walks, and both are handled here:
 *
 * 1. **Tool records are shaped differently.** CodeBuddy calls results
 *    `function_call_result`, where the shared set only knows `tool_result` /
 *    `function_call_output`, writes their output as a text block, and calls a
 *    deferred tool (every MCP tool) through `DeferExecuteTool`. Records are
 *    re-typed and the deferred call unwrapped on the way in, so the shared
 *    part builder sees a shape it understands.
 *
 * 2. **The human ask is not always the raw user text, and not always wrapped.**
 *    When the host injects context it wraps the ask in `<user_query>` and
 *    surrounds it with markup — `<system-reminder>`,
 *    `memory_and_skills_reminder`, `cb_summary`, `conversation_history_summary`,
 *    `identity_context`, and so on. Several of those blocks *quote earlier
 *    turns* (`<previous_user_message>` wraps a whole `<user_query>`), so a
 *    quoted history block must never be mistaken for the current ask. The tag
 *    list and the "<user_query>, else longest candidate" rule mirror the
 *    server-side `workbuddy` ingest adapter (openviking/ingest/sources/
 *    workbuddy.py), which parses this same format.
 *
 *    ⚠️ But measured over real transcripts, **most user records carry no
 *    `<user_query>` at all** — the wrapper only appears when the host decided to
 *    inject context, so requiring it would silently drop every human turn in
 *    those sessions. The authoritative signal for host-authored records is the
 *    record's own metadata: `providerData.isMeta === true` (task notifications,
 *    "continue" nudges) or `providerData.skipRun === true` (slash commands, the
 *    command caveat). Those are dropped outright; everything else falls back to
 *    the host-block-stripped text when no `<user_query>` is present.
 *
 * A third, structural difference: because tool calls are their own records, a
 * *turn* is assembled by aggregating — a `message` record opens a turn and the
 * tool records that follow an assistant message are folded in until the next
 * `message`. Tool activity with no assistant message before it (none at all,
 * or a user message) forms an assistant turn of its own, since tool use is
 * assistant-side.
 *
 * The turn's stable id is the index of the record that opened it: the
 * transcript is append-only, so the position of a turn is a durable cursor.
 */

import { normalizeCaptureRole, sanitizeCapturedText } from "../../memory-plugin-shared/lib/capture-utils.mjs";
import { readUnseenTurns, turnsFromBlocks } from "./incremental-turn-capture.mjs";

export { parseJsonlTranscript as parseTranscript } from "./incremental-turn-capture.mjs";

const TEXT_BLOCK_TYPES = new Set(["text", "input_text", "output_text"]);

// Host-injected blocks inside a user turn. Mirrors the server-side adapter's
// `_HOST_BLOCK_TAGS`; a parity test in tests/codebuddy-turns.test.mjs fails
// when the two drift apart.
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

/** Remove host-injected blocks, leaving whatever the user or host wrote around them. */
export function stripHostBlocks(raw) {
  return String(raw ?? "").replace(HOST_BLOCK_RE, " ").trim();
}

/**
 * The human ask from a user turn, or `""` when there is none.
 *
 * Host blocks are removed first, so a `<user_query>` quoted inside
 * `<previous_user_message>` cannot be mistaken for the current ask. When the
 * record carries no `<user_query>` at all — the common case — the stripped
 * remainder is the ask.
 */
export function userTurnText(raw) {
  const value = typeof raw === "string" ? raw : "";
  if (!value) return "";
  const stripped = value.replace(HOST_BLOCK_RE, " ");
  const queries = [];
  USER_QUERY_RE.lastIndex = 0;
  let match;
  while ((match = USER_QUERY_RE.exec(stripped)) !== null) {
    const candidate = match[1].trim();
    if (candidate) queries.push(candidate);
  }
  if (queries.length === 1) return queries[0];
  if (queries.length === 0) return stripped.trim();
  // Unexpected shape: prefer the longest candidate rather than silently
  // dropping a turn that probably carries the real ask.
  return queries.reduce((a, b) => (b.length > a.length ? b : a));
}

/** Records the host authored rather than the user: notifications, nudges, slash commands. */
function isHostAuthoredRecord(record) {
  const meta = record?.providerData;
  if (!meta || typeof meta !== "object") return false;
  return meta.isMeta === true || meta.skipRun === true;
}

function blocksFrom(content) {
  if (typeof content === "string") return content.trim() ? [content] : [];
  if (!Array.isArray(content)) return [];
  const out = [];
  for (const block of content) {
    if (!block || typeof block !== "object") continue;
    if (!TEXT_BLOCK_TYPES.has(String(block.type || "").toLowerCase())) continue;
    if (typeof block.text === "string" && block.text.trim()) out.push(block.text);
  }
  return out;
}

// Deferred tools (MCP tools among them) are called through `DeferExecuteTool`
// with the real tool in `toolName`; the part names that tool, so its result
// pairs with it and OpenViking's own tools are recognised.
function unwrapDeferredCall(record) {
  let args = record.arguments;
  if (record.name !== "DeferExecuteTool") return { name: record.name, arguments: args };
  if (typeof args === "string") {
    try { args = JSON.parse(args); } catch { return { name: record.name, arguments: record.arguments }; }
  }
  const toolName = args?.toolName;
  if (typeof toolName !== "string" || !toolName) return { name: record.name, arguments: record.arguments };
  return { name: toolName, arguments: args.params ?? {} };
}

// A failed tool call is `incomplete`, but a failed shell command is
// `completed` and says so only in `providerData.toolResult.rawResponse`.
function isErrorResult(record) {
  return record.status === "incomplete" || record.providerData?.toolResult?.rawResponse?.is_error === true;
}

/** CodeBuddy records → the payload shapes `capture-utils` walks. */
function payloadForRecord(record, namesByCallId) {
  const type = String(record?.type || "");
  if (type === "message") {
    const role = normalizeCaptureRole(record.role);
    if (!role) return null;
    const texts = blocksFrom(record.content);
    if (role === "user") {
      // Host-authored records (notifications, "continue" nudges, slash commands)
      // are not user asks, whatever text they carry.
      if (isHostAuthoredRecord(record)) return null;
      // Everything host-injected is removed; what remains is the ask.
      const ask = userTurnText(texts.join("\n"));
      if (!ask) return null;
      return { kind: "message", role, text: ask };
    }
    if (texts.length === 0) return null;
    return { kind: "message", role, text: texts.join("\n") };
  }
  const callId = record?.callId || record?.call_id;
  if (type === "function_call") {
    const call = unwrapDeferredCall(record);
    if (callId) namesByCallId.set(callId, call.name);
    return {
      kind: "tool",
      payload: { type: "function_call", name: call.name, arguments: call.arguments, call_id: callId },
    };
  }
  // `in_progress` marks a streamed partial result; the finished one follows.
  if (type === "function_call_result" && record.status !== "in_progress") {
    const payload = {
      // Re-typed: the shared set has no `function_call_result`.
      type: "tool_result",
      name: namesByCallId.get(callId) || record.name,
      output: record.output,
      call_id: callId,
    };
    if (isErrorResult(record)) payload.is_error = true;
    return { kind: "tool", payload };
  }
  return null;
}

/**
 * Every user/assistant turn in the transcript, in order, each keeping the
 * index of the record that opened it, so the position of a turn is a durable
 * cursor for the incremental capture.
 */
export function extractCodebuddyTurns(records) {
  const collected = [];
  let current = null;
  const open = (role, index) => {
    current = { role, blocks: [], recordIndex: index };
    collected.push(current);
  };

  const namesByCallId = new Map();
  (records || []).forEach((record, index) => {
    const normalized = payloadForRecord(record, namesByCallId);
    if (!normalized) return;
    if (normalized.kind === "message") {
      // A new message always opens a new turn; tool records that follow an
      // assistant message stay attached to it.
      open(normalized.role, index);
      current.blocks.push({ type: "text", text: normalized.text });
    } else {
      // Tool use is assistant-side: after a user message it opens an
      // assistant turn of its own rather than landing in the user's.
      if (current?.role !== "assistant") open("assistant", index);
      current.blocks.push(normalized.payload);
    }
  });
  return turnsFromBlocks(collected);
}

/** The transcript path every CodeBuddy event payload carries, or "". */
export function resolveCodebuddyTranscriptPath(input = {}) {
  return String(input.transcript_path || input.transcriptPath || "").trim();
}

export function extractUnseenCodebuddyTurns(transcriptPath, lastTurnId = null) {
  return readUnseenTurns(transcriptPath, lastTurnId, extractCodebuddyTurns);
}

export function buildCodebuddyTurns(input = {}, state = {}) {
  return extractUnseenCodebuddyTurns(resolveCodebuddyTranscriptPath(input), state.lastTurnId || null);
}

/** The sanitize step the incremental capture asks its host for. */
export function cleanCodebuddyText(value) {
  return sanitizeCapturedText(value == null ? "" : String(value));
}
