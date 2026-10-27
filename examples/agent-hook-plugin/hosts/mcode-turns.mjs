/**
 * Decode MiniMax Code's hook transcript into stable incremental turns.
 *
 * The host hands a hook a Claude Code compatible projection
 * (`<hash>.compatible.jsonl`): every record carries a stable `uuid`, a
 * `message.role`, and a content array of `text`, `tool_use` and `tool_result`
 * blocks. The shared capture helpers already understand that shape, so this
 * module only does the three things that are host-specific:
 *
 *   1. find the projection the payload points at;
 *   2. drop the host's own wrapper around injected context, so a recall block
 *      is not captured back as a new memory;
 *   3. keep the per-record id as the turn id, which is what makes the capture
 *      incremental across the many times the host reports a finished turn.
 */

import { existsSync, readFileSync } from "node:fs";
import {
  collectToolNamesByIdFromEntries,
  extractTextFromPayload,
  sanitizeCapturedText,
} from "../../memory-plugin-shared/lib/capture-utils.mjs";

/** MiniMax Code wraps injected context in this element before the model sees it. */
const HOST_CONTEXT_WRAPPER = /<plugin-hook-context\b[^>]*>[\s\S]*?<\/plugin-hook-context>/gi;

export function cleanMcodeText(value) {
  if (value == null) return "";
  const text = Array.isArray(value)
    ? value.map((part) => (typeof part === "string" ? part : part?.text || "")).filter(Boolean).join("\n")
    : typeof value === "object" && typeof value.text === "string"
      ? value.text
      : String(value);
  return sanitizeCapturedText(String(text).replace(HOST_CONTEXT_WRAPPER, " "));
}

export function resolveMcodeTranscriptPath(input = {}) {
  return String(input.transcript_path || input.transcriptPath || "").trim();
}

export function readMcodeTranscript(path) {
  if (!path) return { status: "missing", entries: [] };
  if (!existsSync(path)) return { status: "missing", entries: [] };
  let raw;
  try {
    raw = readFileSync(path, "utf8");
  } catch (error) {
    return { status: "unreadable", entries: [], error: error?.code || "read_failed" };
  }
  const entries = [];
  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    try {
      entries.push(JSON.parse(line));
    } catch {
      // A half-written last line is not a record; the next capture retries it.
    }
  }
  return { status: "ok", entries };
}

function recordId(entry) {
  return String(entry?.uuid || entry?.id || "").trim() || null;
}

/**
 * The turns the host has not acknowledged yet.
 *
 * The cursor is the last fully captured record, so the transcript is only read
 * from that point forward; the acknowledged-id set in the state then removes
 * the records that were queued but not delivered.
 */
export function buildMcodeTurns(input = {}, state = {}) {
  const transcript = readMcodeTranscript(resolveMcodeTranscriptPath(input));
  if (transcript.status !== "ok") {
    return { status: transcript.status, turns: [], error: transcript.error };
  }

  const entries = transcript.entries;
  const cursor = state.lastTurnId || null;
  let start = 0;
  if (cursor) {
    const index = entries.findIndex((entry) => recordId(entry) === cursor);
    if (index >= 0) start = index + 1;
  }

  const toolNameById = collectToolNamesByIdFromEntries(entries);
  const turns = [];
  for (const entry of entries.slice(start)) {
    const role = entry?.message?.role || entry?.role || entry?.type;
    if (role !== "user" && role !== "assistant") continue;
    const content = cleanMcodeText(extractTextFromPayload(entry, { toolNameById }));
    if (!content) continue;
    turns.push({ role, content, turnId: recordId(entry) });
  }
  return { status: "ok", turns };
}
