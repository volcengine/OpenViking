/**
 * Decode CodeBuddy's transcript into stable incremental user/assistant turns.
 *
 * CodeBuddy writes JSONL where conversation and tool activity live in *separate
 * records*, and where the human ask is wrapped in host-injected markup:
 *
 *   {"type":"message","role":"user"|"assistant","content":[{"type":"input_text"|"output_text"|"text","text":"…"}]}
 *   {"type":"function_call","name":"Bash","arguments":"…","callId":"call_…"}
 *   {"type":"function_call_result","name":"Bash","callId":"call_…","status":"success","output":"…"}
 *   {"type":"reasoning"|"reasoning_text"|"file-history-snapshot"|"summary"|"turn-metrics"|…}
 *
 * Two things differ from what `capture-utils` walks, and both are handled here:
 *
 * 1. **Tool results are named differently.** CodeBuddy calls them
 *    `function_call_result`; the shared set only knows `tool_result` /
 *    `function_call_output`. Records are re-typed on the way in, so the shared
 *    part builder sees a shape it understands. `function_call` is already in
 *    the shared call set, so calls pass through as-is.
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

import { readFileSync } from "node:fs";

import {
  collectToolNamesByIdFromEntries,
  extractPartsFromPayload,
  normalizeCaptureRole,
  sanitizeCapturedText,
} from "../../memory-plugin-shared/lib/capture-utils.mjs";

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

/** CodeBuddy's transcript is JSONL; anything else reads as no records. */
export function parseTranscript(content) {
  const records = [];
  for (const line of String(content || "").split("\n")) {
    if (!line.trim()) continue;
    let record;
    try { record = JSON.parse(line); } catch { continue; }
    // A whole-file JSON array parses as one line too; it is not a record.
    if (!record || typeof record !== "object" || Array.isArray(record)) continue;
    records.push(record);
  }
  return records;
}

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

function formatToolInput(value) {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** CodeBuddy records → the payload shapes `capture-utils` walks. */
function payloadForRecord(record) {
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
  if (type === "function_call") {
    return {
      kind: "tool",
      payload: {
        type: "function_call",
        name: record.name,
        arguments: record.arguments,
        call_id: record.callId || record.call_id,
      },
    };
  }
  if (type === "function_call_result") {
    const payload = {
      // Re-typed: the shared set has no `function_call_result`.
      type: "tool_result",
      name: record.name,
      output: record.output,
      call_id: record.callId || record.call_id,
    };
    // The shared builder derives the part's status from `is_error`; passing
    // CodeBuddy's own "success" through verbatim would surface as an unknown
    // status instead of "completed".
    const status = String(record.status || "").toLowerCase();
    if (status && status !== "success" && status !== "completed") payload.is_error = true;
    return { kind: "tool", payload };
  }
  return null;
}

// The text the capture heuristics read: tool calls inlined with their input.
// Tool *results* stay out of it — the extraction signal lives in the agent's
// prose about what happened, not in the raw bytes a tool returned (measured:
// the shared part builder caps tool input itself and the server externalizes
// oversized output, so retaining result bytes here only diluted the text).
function textFromParts(parts) {
  const chunks = [];
  for (const part of parts) {
    if (part.type !== "tool") {
      chunks.push(part.text);
    } else if (part.tool_status === "running") {
      chunks.push(`[tool: ${part.tool_name || "unknown"}]\n${formatToolInput(part.tool_input)}`);
    }
  }
  return chunks.join("\n\n").trim();
}

/**
 * Every user/assistant turn in the transcript, in order.
 *
 * Turns carrying no structured part are dropped; the rest keep the index of
 * the record that opened them, so the position of a turn is a durable cursor
 * for the incremental capture.
 *
 * Assistant turns are dropped when `captureAssistantTurns` is off — the shared
 * per-turn filter has no notion of the switch, and the agent-hook adapters
 * otherwise capture both sides.
 */
export function extractCodebuddyTurns(records, cfg = {}) {
  const collected = [];
  let current = null;

  const flush = () => {
    if (current && (current.texts.length > 0 || current.tools.length > 0)) collected.push(current);
    current = null;
  };

  (records || []).forEach((record, index) => {
    const normalized = payloadForRecord(record);
    if (!normalized) return;
    if (normalized.kind === "message") {
      // A new message always opens a new turn; tool records that follow an
      // assistant message stay attached to it.
      flush();
      current = { role: normalized.role, texts: [normalized.text], tools: [], recordIndex: index };
    } else {
      // Tool use is assistant-side: after a user message it opens an
      // assistant turn of its own rather than landing in the user's.
      if (current?.role !== "assistant") {
        flush();
        current = { role: "assistant", texts: [], tools: [], recordIndex: index };
      }
      current.tools.push(normalized.payload);
    }
  });
  flush();

  // One payload per turn, in the shape capture-utils walks.
  //
  // Tool blocks go *inline in `content`*, never under a top-level `tool_calls`
  // key: `isToolCallBlock()` tests `Boolean(block.tool_calls)`, so that key
  // makes the whole turn look like a single tool call and swallows its text.
  const turnPayloads = collected.map((turn) => ({
    role: turn.role,
    content: [
      ...turn.texts.map((text) => ({ type: "text", text })),
      ...turn.tools,
    ],
  }));

  const toolNameById = collectToolNamesByIdFromEntries(turnPayloads);

  const turns = [];
  for (const [index, payload] of turnPayloads.entries()) {
    if (payload.role === "assistant" && cfg.captureAssistantTurns === false) continue;
    const parts = extractPartsFromPayload(payload, { toolMaxChars: Infinity, toolNameById });
    if (parts.length === 0) continue;
    const text = textFromParts(parts);
    if (!text) continue;
    turns.push({
      role: normalizeCaptureRole(payload.role),
      content: text,
      turnId: String(collected[index].recordIndex),
    });
  }
  return turns;
}

/** The transcript path every CodeBuddy event payload carries, or "". */
export function resolveCodebuddyTranscriptPath(input = {}) {
  return String(input.transcript_path || input.transcriptPath || "").trim();
}

/**
 * The turns past `lastTurnId`, or a status when the transcript cannot be read.
 *
 * `ok` distinguishes "read it, it has N turns" from "could not read it": a
 * caller must not treat an unreadable transcript as an empty one, or a
 * transient read error would acknowledge turns that were never seen.
 */
export function extractUnseenCodebuddyTurns(transcriptPath, lastTurnId = null, cfg = {}) {
  if (!transcriptPath) return { status: "missing", turns: [] };
  let raw;
  try {
    raw = readFileSync(transcriptPath, "utf8");
  } catch (error) {
    return { status: "unreadable", turns: [], error: error?.code || "read_failed" };
  }
  const turns = extractCodebuddyTurns(parseTranscript(raw), cfg);
  if (turns.length === 0) return { status: "ok", turns: [] };
  const lastIndex = lastTurnId == null || lastTurnId === ""
    ? -1
    : turns.findIndex((turn) => turn.turnId === String(lastTurnId));
  return { status: "ok", turns: lastIndex === -1 ? turns : turns.slice(lastIndex + 1) };
}

export function buildCodebuddyTurns(input = {}, state = {}, cfg = {}) {
  return extractUnseenCodebuddyTurns(
    resolveCodebuddyTranscriptPath(input),
    state.lastTurnId || null,
    cfg,
  );
}

/** The sanitize step the incremental capture asks its host for. */
export function cleanCodebuddyText(value) {
  return sanitizeCapturedText(value == null ? "" : String(value));
}
