import { readFileSync } from "node:fs";

import { stableHash } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { shouldCaptureText } from "../../memory-plugin-shared/lib/capture-utils.mjs";

export function turnDedupKey(turn) {
  return turn.turnId
    ? `${turn.turnId}:${turn.role}`
    : stableHash(turn.role, turn.content);
}

export function buildIncrementalCapturePlan(turns, state = {}, cfg = {}) {
  const acknowledged = new Set(
    Array.isArray(state.capturedTurnIds) ? state.capturedTurnIds : [],
  );
  const candidates = [];
  for (const turn of turns) {
    const decision = shouldCaptureText(turn.content, turn.role, cfg);
    if (!decision.shouldCapture) continue;
    candidates.push({ dedupKey: turnDedupKey(turn), turn, content: decision.text });
  }
  const seenInBatch = new Set();
  const toSend = candidates.filter((item) => {
    if (acknowledged.has(item.dedupKey)) return false;
    const batchKey = stableHash(item.dedupKey, item.content);
    if (seenInBatch.has(batchKey)) return false;
    seenInBatch.add(batchKey);
    return true;
  });
  const payloads = toSend.map(({ turn, content }) => ({
    role: turn.role,
    content,
    ...(turn.turnId ? { turn_id: turn.turnId } : {}),
  }));
  return { candidates, toSend, payloads };
}

function acknowledgedCursor(candidates, acknowledged) {
  let cursor = null;
  let currentTurnId = null;
  let currentComplete = true;

  for (const item of candidates) {
    const turnId = item.turn.turnId || null;
    if (!turnId) continue;
    if (currentTurnId !== turnId) {
      if (currentTurnId && currentComplete) cursor = currentTurnId;
      if (currentTurnId && !currentComplete) return cursor;
      currentTurnId = turnId;
      currentComplete = true;
    }
    if (!acknowledged.has(item.dedupKey)) currentComplete = false;
  }

  if (currentTurnId && currentComplete) cursor = currentTurnId;
  return cursor;
}

export function applyIncrementalCaptureResult(state, plan, result, cleanText) {
  const acknowledged = new Set(
    Array.isArray(state.capturedTurnIds) ? state.capturedTurnIds : [],
  );
  const captured = Math.min(
    plan.toSend.length,
    Math.max(0, Number(result?.sent || 0) + Number(result?.queued || 0)),
  );
  for (const item of plan.toSend.slice(0, captured)) acknowledged.add(item.dedupKey);

  const cursor = acknowledgedCursor(plan.candidates, acknowledged);
  const pendingPrompt = cleanText(state.pendingPrompt?.prompt || "");
  let pendingPromptItem = null;
  if (pendingPrompt) {
    for (let index = plan.candidates.length - 1; index >= 0; index--) {
      const item = plan.candidates[index];
      if (item.turn.role === "user" && cleanText(item.turn.content) === pendingPrompt) {
        pendingPromptItem = item;
        break;
      }
    }
  }

  return {
    ...state,
    capturedTurnIds: [...acknowledged].slice(-1000),
    pendingPrompt: pendingPromptItem && acknowledged.has(pendingPromptItem.dedupKey)
      ? null
      : state.pendingPrompt,
    lastTurnId: cursor || state.lastTurnId || null,
    captured,
  };
}

/** A JSONL transcript's records; anything else reads as no records. */
export function parseJsonlTranscript(content) {
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

/**
 * The turns `extract` finds past `lastTurnId` in a JSONL transcript, or a
 * status when the transcript cannot be read. `ok` distinguishes "read it, it
 * has N turns" from "could not read it": a caller must not treat an unreadable
 * transcript as an empty one, or a transient read error would acknowledge
 * turns that were never seen.
 */
export function readUnseenTurns(transcriptPath, lastTurnId, extract) {
  if (!transcriptPath) return { status: "missing", turns: [] };
  let raw;
  try {
    raw = readFileSync(transcriptPath, "utf8");
  } catch (error) {
    return { status: "unreadable", turns: [], error: error?.code || "read_failed" };
  }
  const turns = extract(parseJsonlTranscript(raw));
  if (turns.length === 0) return { status: "ok", turns: [] };
  const lastIndex = lastTurnId == null || lastTurnId === ""
    ? -1
    : turns.findIndex((turn) => turn.turnId === String(lastTurnId));
  return { status: "ok", turns: lastIndex === -1 ? turns : turns.slice(lastIndex + 1) };
}

function formatToolInput(value) {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

// Tool calls are inlined with their input. Tool *results* stay out — the
// extraction signal lives in the agent's prose about what happened, not in the
// raw bytes a tool returned (measured: the shared part builder caps tool input
// itself and the server externalizes oversized output, so retaining result
// bytes here only diluted the text).
export function textFromToolParts(parts) {
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

/** Commit at session end and on compaction, otherwise once the turn threshold is reached. */
export function shouldCommitCapture(event, cfg = {}, previous = 0, captured = 0) {
  const uncommitted = Number(previous || 0) + captured;
  if (uncommitted <= 0) return false;
  if (event === "session-end") return true;
  if (event === "pre-compact") return cfg.autoCommitOnCompact !== false;
  return uncommitted >= Number(cfg.commitTurnThreshold || Infinity);
}
