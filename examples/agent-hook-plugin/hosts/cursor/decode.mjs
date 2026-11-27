/**
 * Decode Cursor's agent transcript, plus the tool results its hooks saw, into
 * incremental turns.
 *
 * The transcript holds `{ role, message: { content } }` lines with text only and
 * no message id, so a text turn is known by its position and text: two
 * identical turns at different positions are both kept. Tool calls never reach
 * the transcript; postToolUse / postToolUseFailure / afterMCPExecution notes
 * carry them, each placed after the text turns the transcript held when it ran.
 */

import { stableHash } from "../../lib/agent-hook-runtime.mjs";
import { parseJsonl, resumeTurns } from "../../lib/flow/turns.mjs";

const HOST_BLOCK_TAGS = [
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
const HOST_BLOCK_RE = new RegExp(`<(?<tag>${HOST_BLOCK_TAGS.join("|")})(?:\\s[^>]*)?>.*?</\\k<tag>\\s*>`, "gis");
const USER_QUERY_RE = /<user_query\s*>(.*?)<\/user_query\s*>/gis;
const TIMESTAMP_RE = /<timestamp\s*>.*?<\/timestamp\s*>/gis;

function contentText(content) {
  if (typeof content === "string") return content.trim();
  if (!Array.isArray(content)) return "";
  return content
    .filter((part) => part?.type === "text" && typeof part.text === "string")
    .map((part) => part.text)
    .join("\n")
    .replace(/\[REDACTED\]/g, "")
    .trim();
}

// Cursor stamps each ask and wraps it: `<timestamp>…</timestamp><user_query>…</user_query>`.
function userText(text) {
  const stripped = text.replace(TIMESTAMP_RE, " ").replace(HOST_BLOCK_RE, " ");
  const queries = [...stripped.matchAll(USER_QUERY_RE)].map((match) => match[1].trim()).filter(Boolean);
  if (queries.length === 0) return stripped.trim();
  return queries.reduce((a, b) => (b.length > a.length ? b : a));
}

/** The transcript's text turns, `{ role, content }`. */
export function parseCursorTranscript(text) {
  const turns = [];
  for (const record of parseJsonl(text)) {
    if (record.role !== "user" && record.role !== "assistant") continue;
    const raw = contentText(record.message?.content ?? record.content);
    const content = record.role === "user" ? userText(raw) : raw;
    if (content) turns.push({ role: record.role, content });
  }
  return turns;
}

const textKey = (index, turn) => stableHash(index, turn.role, turn.content);
const toolKey = (id) => `tool:${id}`;

/** State from before the flow kept `capturedThrough` / `capturedRecentHashes` / `capturedHashes` / `capturedToolIds`. */
function legacyCursor(state) {
  if (!state || Array.isArray(state.capturedTurnIds)) return state;
  const known = [
    ...(Array.isArray(state.capturedRecentHashes) ? state.capturedRecentHashes : []),
    ...(state.capturedThroughHash ? [state.capturedThroughHash] : []),
    ...(Array.isArray(state.capturedHashes) ? state.capturedHashes : []),
    ...(Array.isArray(state.capturedToolIds) ? state.capturedToolIds.map(toolKey) : []),
  ];
  const lastTurnId = Number.isInteger(state.capturedThrough) && state.capturedThrough >= 0
    ? String(state.capturedThrough)
    : null;
  return { lastTurnId, capturedTurnIds: known };
}

/** @param {import("../../lib/flow/host.d.mts").DecodeInput} input */
export function decode({ text, cursor, notes }) {
  const texts = parseCursorTranscript(text);
  const all = [];
  const keys = new Map();
  const push = (turn, key) => { keys.set(turn, key); all.push(turn); };
  let next = 0;
  let after = 0;
  for (const note of notes || []) {
    const part = note?.tool;
    if (!part?.tool_use_id) continue;
    // Keep note order even when a compaction shortened the transcript.
    after = Math.max(after, Number.isInteger(note.afterTurn) ? note.afterTurn : texts.length);
    for (; next < Math.min(after, texts.length); next += 1) {
      push({ ...texts[next], turnId: String(next) }, textKey(next, texts[next]));
    }
    push({ role: "assistant", content: "", payload: part }, toolKey(part.tool_use_id));
  }
  for (; next < texts.length; next += 1) push({ ...texts[next], turnId: String(next) }, textKey(next, texts[next]));

  let start = legacyCursor(cursor);
  // A transcript rewritten at or before the cursor's turn reads again from the
  // start; what is unchanged is still known by position and text.
  const at = start?.lastTurnId == null ? null : all.find((turn) => turn.turnId === String(start.lastTurnId));
  if (at && !start.capturedTurnIds.includes(keys.get(at))) start = { ...start, lastTurnId: null };
  if (!at && start?.lastTurnId != null) start = { ...start, lastTurnId: null };
  return resumeTurns(all, start, { key: (turn) => keys.get(turn) });
}
