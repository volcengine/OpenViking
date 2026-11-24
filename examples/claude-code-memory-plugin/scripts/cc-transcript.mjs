/**
 * Claude Code transcript reading, on top of the shared capture utilities.
 *
 * Claude writes JSONL of Anthropic messages, whose blocks differ from the shape
 * `capture-utils` walks in one way that matters: a `tool_result` nests its
 * output in a content array of text blocks and names the call it answers by id
 * rather than by name. Normalising that is all this harness needs of its own —
 * the block walking, the part building and the capture filter are shared.
 */

import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { open, readFile } from "node:fs/promises";

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

function normalizeMessage(message, cfg) {
  if (!message || typeof message !== "object") return null;
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

/**
 * Every user/assistant turn in the transcript, in order.
 *
 * Turns carrying no structured part are dropped; the rest keep their index, so
 * a caller can treat the position of a turn as a durable cursor.
 */
export function extractCaptureTurns(messages, cfg = {}) {
  const payloads = (messages || []).map((message) => normalizeMessage(message, cfg));
  const toolNameById = collectToolNamesByIdFromEntries(payloads.filter(Boolean));
  const turns = [];
  for (const payload of payloads) {
    const turn = turnFromPayload(payload, toolNameById);
    if (turn) turns.push(turn);
  }
  return turns;
}

function turnFromPayload(payload, toolNameById) {
  const role = payload && normalizeCaptureRole(payload.role);
  if (!role) return null;
  // Output was capped in normalizeBlock; capping it again here would eat the
  // "[truncated, N more chars]" note that cap just appended.
  const parts = extractPartsFromPayload(payload, { toolMaxChars: Infinity, toolNameById });
  if (parts.length === 0) return null;
  return { role, text: textFromParts(parts), parts };
}

// ---------------------------------------------------------------------------
// Incremental reading
// ---------------------------------------------------------------------------

// A transcript only grows, and a long session's grows past what one string can
// hold: Node caps a string at 2^29-24 UTF-16 units, and readFile(path, "utf-8")
// of a larger file rejects with "RangeError: Invalid string length". So the file
// is streamed a line at a time, and a byte cursor says where the next read may
// start.

const CURSOR_HEAD_BYTES = 256;
const CURSOR_MAX_OPEN_TOOLS = 256;
const NEWLINE = 0x0a;

function headDigest(bytes) {
  return createHash("sha1").update(bytes).digest("hex").slice(0, 16);
}

/** Lines of the file from byte `start`, each with the offset it begins at. */
async function* readLines(path, start) {
  let held = [];
  let offset = start;
  for await (const chunk of createReadStream(path, { start, highWaterMark: 1 << 20 })) {
    let from = 0;
    for (;;) {
      const end = chunk.indexOf(NEWLINE, from);
      if (end === -1) break;
      const piece = chunk.subarray(from, end);
      const bytes = held.length ? Buffer.concat([...held, piece]) : piece;
      held = [];
      yield { bytes, offset };
      offset += bytes.length + 1;
      from = end + 1;
    }
    if (from < chunk.length) held.push(chunk.subarray(from));
  }
  // A last line with no newline yet: Claude may still be writing it.
  if (held.length) yield { bytes: Buffer.concat(held), offset };
}

function parseLine(bytes) {
  let line;
  // A single line beyond the string cap cannot be decoded; skip it as the
  // whole-file parse skipped any line it could not parse.
  try { line = bytes.toString("utf-8"); } catch { return null; }
  if (!line.trim()) return null;
  try { return JSON.parse(line); } catch { return null; }
}

async function readAt(handle, position, length) {
  const buffer = Buffer.alloc(length);
  const { bytesRead } = await handle.read(buffer, 0, length, position);
  return buffer.subarray(0, bytesRead);
}

/** Whether `cursor` still names a line of this file, i.e. a place to resume. */
async function cursorHolds(handle, size, cursor, capturedTurnCount) {
  if (!cursor || typeof cursor !== "object") return false;
  const { offset, turnCount, head, headBytes } = cursor;
  if (!Number.isSafeInteger(offset) || offset < 0 || offset >= size) return false;
  // A cursor ahead of the capture cursor would skip turns still to be sent
  // (the capture cursor is rewound when the server lost the session).
  if (!Number.isSafeInteger(turnCount) || turnCount < 0 || turnCount > capturedTurnCount) return false;
  if (!Number.isSafeInteger(headBytes) || headBytes <= 0 || headBytes > CURSOR_HEAD_BYTES) return false;
  if (offset > 0 && (await readAt(handle, offset - 1, 1))[0] !== NEWLINE) return false;
  const bytes = await readAt(handle, offset, headBytes);
  return bytes.length === headBytes && headDigest(bytes) === head;
}

async function startsWithArray(handle) {
  const text = (await readAt(handle, 0, 4096)).toString("utf-8").replace(/^\uFEFF/, "").trimStart();
  return text.startsWith("[");
}

/**
 * Turns of the JSONL from byte `start` on, or null when `start` was a cursor
 * and its line no longer opens with a user turn (so its count cannot be trusted).
 *
 * Kept turns begin at the last user turn before `capturedTurnCount`: the new
 * turns, plus what the final-answer check reads back to. Earlier turns are
 * counted and dropped, so memory holds the new part of the file, not all of it.
 */
async function scanLines(path, cfg, capturedTurnCount, cursor) {
  const start = cursor ? cursor.offset : 0;
  const toolNameById = { ...(cursor?.toolNames || {}) };
  const openTools = new Map(Object.entries(toolNameById));
  let totalTurns = cursor ? cursor.turnCount : 0;
  let firstIndex = totalTurns;
  let turns = [];
  let next = null;
  let atCursorLine = Boolean(cursor);

  for await (const { bytes, offset } of readLines(path, start)) {
    const message = parseLine(bytes);
    const payload = message === null ? null : normalizeMessage(message, cfg);
    const calls = payload ? collectToolNamesByIdFromEntries([payload]) : {};
    Object.assign(toolNameById, calls);
    const turn = turnFromPayload(payload, toolNameById);

    if (atCursorLine) {
      atCursorLine = false;
      if (turn?.role !== "user") return null;
    }

    const openBefore = turn?.role === "user" && openTools.size ? Object.fromEntries(openTools) : null;
    for (const [id, name] of Object.entries(calls)) openTools.set(id, name);
    if (!turn) continue;
    for (const part of turn.parts) {
      if (part.type === "tool" && part.tool_status !== "running" && part.tool_id) openTools.delete(part.tool_id);
    }
    while (openTools.size > CURSOR_MAX_OPEN_TOOLS) openTools.delete(openTools.keys().next().value);

    if (turn.role === "user") {
      // Resuming here re-reads this line, so it carries what the line itself
      // could not supply: the count before it, and the names of calls whose
      // results had not arrived yet.
      const headBytes = Math.min(bytes.length, CURSOR_HEAD_BYTES);
      next = {
        offset,
        turnCount: totalTurns,
        head: headDigest(bytes.subarray(0, headBytes)),
        headBytes,
        ...(openBefore ? { toolNames: openBefore } : {}),
      };
      if (totalTurns < capturedTurnCount) {
        turns = [];
        firstIndex = totalTurns;
      }
    }
    turns.push(turn);
    totalTurns += 1;
  }
  return { totalTurns, firstIndex, turns, cursor: next, readFrom: start };
}

/**
 * The transcript's capture turns from where the last capture left off.
 *
 * Returns `turns` — the tail of the transcript starting at index `firstIndex`,
 * which is never past `capturedTurnCount` — with `totalTurns` counting every
 * turn in the file, exactly as `extractCaptureTurns(parseTranscript(file))`
 * would index them. `cursor` is what to pass back next time so only the bytes
 * added since are read; without one (or when it no longer matches the file)
 * the whole file is streamed once.
 */
export async function readCaptureTurns(transcriptPath, cfg = {}, { capturedTurnCount = 0, cursor = null } = {}) {
  const captured = Number.isSafeInteger(capturedTurnCount) && capturedTurnCount > 0 ? capturedTurnCount : 0;
  let resume = null;
  const handle = await open(transcriptPath, "r");
  try {
    const { size } = await handle.stat();
    if (size === 0) return { totalTurns: 0, firstIndex: 0, turns: [], cursor: null, readFrom: 0 };
    if (await startsWithArray(handle)) {
      // The older single-array form has no lines to resume from, and predates
      // the sessions long enough to need it.
      const turns = extractCaptureTurns(parseTranscript(await readFile(transcriptPath, "utf-8")), cfg);
      return { totalTurns: turns.length, firstIndex: 0, turns, cursor: null, readFrom: 0 };
    }
    if (await cursorHolds(handle, size, cursor, captured)) resume = cursor;
  } finally {
    await handle.close();
  }
  return (resume && await scanLines(transcriptPath, cfg, captured, resume))
    || scanLines(transcriptPath, cfg, captured, null);
}
