/**
 * Decode Claude Code's transcript into the turns not yet captured.
 *
 * Claude Code writes JSONL of Anthropic messages wrapped in bookkeeping
 * records; older transcripts are one JSON array. A `tool_result` nests its
 * output in a content array of text blocks and names the call it answers by id,
 * so it is flattened before the part builder sees it.
 *
 * The cursor is `{ capturedTurnCount }`: an index into every user/assistant
 * record that yields a part. Host-authored records in the user's role (the
 * compaction summary, `isMeta` skill bodies and command caveats) are counted
 * but never sent, so a cursor written before they were recognised still lines
 * up.
 */

import {
  collectToolNamesByIdFromEntries,
  extractPartsFromPayload,
  normalizeCaptureRole,
} from "../../lib/capture-utils.mjs";

export function parseTranscript(text) {
  try {
    const data = JSON.parse(text);
    if (Array.isArray(data)) return data;
  } catch { /* JSONL */ }
  const records = [];
  for (const line of String(text || "").split("\n")) {
    if (!line.trim()) continue;
    try { records.push(JSON.parse(line)); } catch { /* skip */ }
  }
  return records;
}

function toolResultText(content) {
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
    output: toolResultText(block.content),
    is_error: Boolean(block.is_error),
  };
}

function normalizeRecord(record) {
  if (!record || typeof record !== "object") return null;
  let role = record.role;
  let content;
  let hostAuthored = false;
  if (record.content !== undefined) {
    content = record.content;
  } else if (record.message && typeof record.message === "object") {
    // Bookkeeping records (e.g. `api-request-blob`) repeat a message already in
    // the transcript; only user/assistant records are the conversation.
    if (record.type !== undefined && record.type !== "user" && record.type !== "assistant") return null;
    hostAuthored = record.isCompactSummary === true || record.isMeta === true;
    role = record.message.role || role;
    content = record.message.content;
  }
  return {
    role,
    content: Array.isArray(content) ? content.map(normalizeBlock) : content,
    hostAuthored,
  };
}

/** Every turn the cursor counts, in order: `{ role, payload, parts, hostAuthored }`. */
export function transcriptTurns(records) {
  const payloads = (records || []).map(normalizeRecord);
  const toolNames = collectToolNamesByIdFromEntries(payloads.filter(Boolean));
  const turns = [];
  for (const payload of payloads) {
    const role = payload && normalizeCaptureRole(payload.role);
    if (!role) continue;
    const parts = extractPartsFromPayload(payload, { toolMaxChars: Infinity, toolNameById: toolNames });
    if (parts.length === 0) continue;
    turns.push({ role, payload: { role, content: payload.content }, parts, hostAuthored: payload.hostAuthored });
  }
  return { turns, toolNames };
}

const textOf = (parts) => parts.filter((part) => part.type === "text").map((part) => part.text);

// last_assistant_message is the text of the turn's final message, which
// follows its last tool result; the transcript writes each block as a line.
function holdsAnswer(turns, answer) {
  const start = turns.findLastIndex((turn) => turn.role === "user") + 1;
  const text = turns.slice(start).flatMap((turn) => textOf(turn.parts)).join("");
  return text.replace(/\s+/g, "") === answer.replace(/\s+/g, "");
}

/** @param {import("../../lib/flow/host.d.mts").DecodeInput} input */
export function decode({ text, cursor, payload = {}, cfg = {} }) {
  const { turns: all, toolNames } = transcriptTurns(parseTranscript(text));
  const start = Math.min(Math.max(0, Number(cursor?.capturedTurnCount) || 0), all.length);
  const turns = all.slice(start).map((turn) => (turn.hostAuthored
    ? { role: turn.role, content: "", payload: { role: turn.role, content: [] } }
    : { role: turn.role, content: textOf(turn.parts).join("\n\n"), payload: turn.payload }));
  const counted = turns.length;
  // Claude Code writes the transcript asynchronously, so at Stop it may not
  // hold the final answer yet. The answer taken from the payload is left out of
  // the cursor: should the transcript catch up later, it goes out twice rather
  // than not at all.
  const answer = cfg.captureLastAssistantOnStop && !payload.agent_transcript_path
    ? String(payload.last_assistant_message || "").trim()
    : "";
  if (answer && !holdsAnswer(all, answer)) {
    turns.push({ role: "assistant", content: answer, payload: { role: "assistant", content: [{ type: "text", text: answer }] } });
  }
  return {
    turns,
    toolNames,
    cursorAfter: (count) => ({ capturedTurnCount: start + Math.min(count, counted) }),
  };
}
