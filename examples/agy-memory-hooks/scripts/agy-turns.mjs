import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

const USER_SOURCES = new Set(["USER_INPUT", "USER_EXPLICIT", "USER", "HUMAN"]);
const ASSISTANT_SOURCES = new Set(["MODEL", "AGENT", "ASSISTANT"]);

// agy marks every transcript record with the actor that produced it (`source`)
// and with what the record *is* (`type`). Tool results are emitted under the
// model's own source (`MODEL/VIEW_FILE`, `MODEL/RUN_COMMAND`, …) and IDE edit
// notices under the user's (`USER_EXPLICIT/CODE_ACTION`), so filtering by
// source alone would capture raw command output and file dumps as turns. Only
// the conversational record types are kept; a record with no `type` at all is
// allowed through so other agy builds keep working.
const USER_TYPES = new Set(["USER_INPUT"]);
const ASSISTANT_TYPES = new Set(["PLANNER_RESPONSE"]);

export function stableAgyHash(...values) {
  return createHash("sha256")
    .update(values.map((value) => String(value ?? "")).join("\n"))
    .digest("hex");
}

export function cleanAgyText(value) {
  return String(value || "")
    .replace(/<openviking-context\b[^>]*>[\s\S]*?<\/openviking-context>/gi, "")
    .replace(/<relevant-memories>[\s\S]*?<\/relevant-memories>/gi, "")
    .replace(/<ADDITIONAL_METADATA>[\s\S]*?<\/ADDITIONAL_METADATA>/gi, "")
    .replace(/<\/?USER_REQUEST>/g, "")
    .trim();
}

function extractText(content) {
  if (typeof content === "string") return cleanAgyText(content);
  if (content == null) return "";
  if (typeof content === "object") {
    if (typeof content.text === "string") return cleanAgyText(content.text);
    if (Array.isArray(content)) {
      const parts = [];
      for (const part of content) {
        if (!part || typeof part !== "object") continue;
        if (typeof part.text === "string" && part.text.trim()) parts.push(cleanAgyText(part.text));
        if (typeof part.content === "string" && part.content.trim()) parts.push(cleanAgyText(part.content));
      }
      if (parts.length) return parts.join("\n");
    }
  }
  return "";
}

export function readAgyTranscript(transcriptPath) {
  if (!transcriptPath) return [];
  let text = "";
  try {
    text = readFileSync(transcriptPath, "utf8");
  } catch {
    return [];
  }
  const records = [];
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      const record = JSON.parse(trimmed);
      if (record && typeof record === "object") records.push(record);
    } catch {}
  }
  return records;
}

export function recordRole(record) {
  const source = String(record.source || record.sender || "").toUpperCase();
  const type = String(record.type || "").toUpperCase();
  if (USER_SOURCES.has(source)) return !type || USER_TYPES.has(type) ? "user" : null;
  if (ASSISTANT_SOURCES.has(source)) return !type || ASSISTANT_TYPES.has(type) ? "assistant" : null;
  return null;
}

function recordStep(record) {
  const step = Number(record.step_index ?? record.stepIndex ?? record.step_idx ?? record.seq ?? -1);
  return Number.isFinite(step) && step >= 0 ? step : null;
}

export function latestAgyPrompt(input = {}, state = {}) {
  const records = readAgyTranscript(input.transcriptPath || input.transcript_path);
  for (let index = records.length - 1; index >= 0; index--) {
    const record = records[index];
    if (recordRole(record) !== "user") continue;
    const content = extractText(record.content);
    if (content) return content;
  }
  return cleanAgyText(state.pendingPrompt?.prompt);
}

export function buildAgyTurns(input = {}, state = {}) {
  const records = readAgyTranscript(input.transcriptPath || input.transcript_path);
  const turns = [];
  for (const record of records) {
    const step = recordStep(record);
    if (step === null) continue;
    const role = recordRole(record);
    if (!role) continue;
    const content = extractText(record.content);
    if (!content) continue;
    turns.push({ step, stepKey: `s:${step}`, role, content });
  }
  if (turns.length === 0) {
    const pending = state.pendingPrompt;
    if (pending?.prompt && cleanAgyText(pending.prompt)) {
      return [{
        stepKey: `p:${pending.hash || stableAgyHash(pending.prompt)}`,
        role: "user",
        content: cleanAgyText(pending.prompt),
      }];
    }
    return [];
  }
  // agy flushes the transcript asynchronously, so records can land out of
  // order around `Stop` (the user request step is sometimes persisted after the
  // model step). `step_index` is the authoritative order and must be compared
  // numerically: sorting the `s:<n>` keys as strings puts `s:10` before `s:2`.
  turns.sort((a, b) => a.step - b.step);
  return turns.map(({ stepKey, role, content }) => ({ stepKey, role, content }));
}
