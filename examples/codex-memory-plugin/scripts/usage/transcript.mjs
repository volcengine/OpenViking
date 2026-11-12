import { open } from "node:fs/promises";
import { parseRecall } from "./sources.mjs";

const MAX_BYTES = 8 * 1024 * 1024;

// Codex rollout JSONL is not a stable API. Require an identified turn and
// accept only developer messages, never user text or tool/assistant output.

/** Walk rollout records, yielding each with the Codex turn it belongs to. */
export function* recordsWithTurn(records) {
  let currentTurn = null;
  for (const record of records) {
    const item = record?.payload;
    if (!item || typeof item !== "object") {
      yield { record, turnId: currentTurn };
      continue;
    }
    if (record.type === "turn_context" ||
        (record.type === "event_msg" && item.type === "task_started")) {
      currentTurn = item.turn_id || null;
    }
    yield { record, turnId: item.internal_chat_message_metadata_passthrough?.turn_id || currentTurn };
  }
}

/** What auto-recall injected through one developer message record. */
export function recallInRecord(record) {
  const item = record?.payload;
  if (record?.type !== "response_item" || item?.type !== "message" ||
      item.role !== "developer" || !Array.isArray(item.content)) return [];
  const recalled = [];
  for (const part of item.content) {
    if (part?.type !== "input_text" || typeof part.text !== "string") continue;
    for (const block of part.text.matchAll(/<openviking-context\b([^>]*)>([\s\S]*?)<\/openviking-context>/g)) {
      if (/\bsource\s*=/.test(block[1])) continue;
      recalled.push(...parseRecall(block[2]));
    }
  }
  return recalled;
}

export function recallFromTranscript(text, turnId) {
  const records = [];
  for (const line of String(text).split("\n")) {
    try { records.push(JSON.parse(line)); } catch { /* skip */ }
  }
  const recalled = new Map();
  for (const { record, turnId: itemTurn } of recordsWithTurn(records)) {
    if (itemTurn !== turnId) continue;
    for (const entry of recallInRecord(record)) recalled.set(entry.uri, entry);
  }
  return [...recalled.values()];
}

export async function readTranscriptRecall(path, turnId) {
  if (typeof path !== "string" || !path || !turnId) return [];
  let file;
  try {
    file = await open(path, "r");
    const info = await file.stat();
    if (!info.isFile()) return [];
    const start = Math.max(0, info.size - MAX_BYTES);
    const buffer = Buffer.alloc(Math.min(info.size, MAX_BYTES));
    const { bytesRead } = await file.read(buffer, 0, buffer.length, start);
    let text = buffer.subarray(0, bytesRead).toString("utf8");
    if (start > 0) text = text.slice(text.indexOf("\n") + 1);
    return recallFromTranscript(text, turnId);
  } catch {
    return [];
  } finally {
    await file?.close().catch(() => {});
  }
}
