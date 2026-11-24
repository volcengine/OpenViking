/**
 * Decode Qoder CLI's transcript into stable incremental user/assistant turns.
 *
 * Qoder writes Claude Code-style JSONL (hosts/qoder/DESIGN.md §8), with two
 * differences that shape this decoder:
 *
 * 1. **One assistant record per content block.** A model response is split
 *    into `text` / `tool_use` / `thinking` records that share a
 *    `responseGroupId`. They are folded back into one assistant turn, together
 *    with the `tool_result` user records that answer its calls; `thinking` is
 *    dropped.
 *
 * 2. **Host context is written to the transcript.** What a hook injects lands
 *    in `attachment` records (`hook_output`, `hook_success`), beside the skill
 *    listing, environment and session context. Only `user` and `assistant`
 *    records are read, so the recalled memories this plugin injects are never
 *    captured back.
 *
 * A `user` record is a human ask only when it carries text, is not host-authored
 * (`isMeta` skill bodies, the `isCompactSummary` summary, a non-human
 * `origin.kind`) and is not a subagent's (`isSidechain`). Tool results arrive as
 * `user` records too and stay in the assistant turn they answer.
 *
 * The turn's stable id is the index of the record that opened it: the
 * transcript is append-only, compaction and resume included, so the position
 * of a turn is a durable cursor.
 */

import {
  collectToolNamesByIdFromEntries,
  extractPartsFromPayload,
  normalizeCaptureRole,
  sanitizeCapturedText,
} from "../../memory-plugin-shared/lib/capture-utils.mjs";
import { readUnseenTurns, textFromToolParts } from "./incremental-turn-capture.mjs";

export { parseJsonlTranscript as parseTranscript } from "./incremental-turn-capture.mjs";

function contentBlocks(record) {
  const content = record?.message?.content;
  if (typeof content === "string") return content.trim() ? [{ type: "text", text: content }] : [];
  return Array.isArray(content) ? content.filter((block) => block && typeof block === "object") : [];
}

function textsOf(blocks) {
  return blocks
    .filter((block) => block.type === "text" && typeof block.text === "string" && block.text.trim())
    .map((block) => block.text);
}

function isHostAuthoredUser(record) {
  if (record.isMeta === true || record.isCompactSummary === true) return true;
  const kind = record.origin?.kind;
  return kind != null && kind !== "human";
}

/** Qoder records → what the turn aggregation needs, or null for host records. */
function decodeRecord(record) {
  if (record?.isSidechain === true) return null;
  const blocks = contentBlocks(record);
  if (record?.type === "user") {
    const results = blocks.filter((block) => block.type === "tool_result");
    if (results.length > 0) return { kind: "tool", tools: results };
    if (isHostAuthoredUser(record)) return null;
    const texts = textsOf(blocks);
    return texts.length > 0 ? { kind: "user", texts } : null;
  }
  if (record?.type === "assistant") {
    const texts = textsOf(blocks);
    const tools = blocks.filter((block) => block.type === "tool_use");
    if (texts.length === 0 && tools.length === 0) return null;
    return { kind: "assistant", group: record.responseGroupId || null, texts, tools };
  }
  return null;
}

export function extractQoderTurns(records, cfg = {}) {
  const collected = [];
  let current = null;

  const flush = () => {
    if (current && (current.texts.length > 0 || current.tools.length > 0)) collected.push(current);
    current = null;
  };
  const openAssistant = (index, group) => {
    flush();
    current = { role: "assistant", group, texts: [], tools: [], recordIndex: index };
  };

  (records || []).forEach((record, index) => {
    const decoded = decodeRecord(record);
    if (!decoded) return;
    if (decoded.kind === "user") {
      flush();
      current = { role: "user", texts: decoded.texts, tools: [], recordIndex: index };
      return;
    }
    if (decoded.kind === "assistant") {
      // A new model response opens a new turn; its other blocks, and the tool
      // results answering it, stay in that turn.
      if (current?.role !== "assistant" || (decoded.group && current.group !== decoded.group)) {
        openAssistant(index, decoded.group);
      }
      current.texts.push(...decoded.texts);
      current.tools.push(...decoded.tools);
      return;
    }
    if (current?.role !== "assistant") openAssistant(index, null);
    current.tools.push(...decoded.tools);
  });
  flush();

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
    const text = textFromToolParts(parts);
    if (!text) continue;
    turns.push({
      role: normalizeCaptureRole(payload.role),
      content: text,
      turnId: String(collected[index].recordIndex),
    });
  }
  return turns;
}

export function resolveQoderTranscriptPath(input = {}) {
  return String(input.transcript_path || input.transcriptPath || "").trim();
}

export function extractUnseenQoderTurns(transcriptPath, lastTurnId = null, cfg = {}) {
  return readUnseenTurns(transcriptPath, lastTurnId, (records) => extractQoderTurns(records, cfg));
}

export function buildQoderTurns(input = {}, state = {}, cfg = {}) {
  return extractUnseenQoderTurns(
    resolveQoderTranscriptPath(input),
    state.lastTurnId || null,
    cfg,
  );
}

export function cleanQoderText(value) {
  return sanitizeCapturedText(value == null ? "" : String(value));
}
