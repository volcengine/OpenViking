import { readFileSync } from "node:fs";

import {
  addAgentMessages,
  commitAgentSession,
  getAgentSession,
  stableHash,
} from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import {
  buildCaptureMessage,
  collectToolNamesByIdFromEntries,
  normalizeCaptureRole,
} from "../../memory-plugin-shared/lib/capture-utils.mjs";
import { thresholdCommitDue } from "../../memory-plugin-shared/lib/session-model.mjs";

export function turnDedupKey(turn) {
  return turn.turnId
    ? `${turn.turnId}:${turn.role}`
    : stableHash(turn.role, turn.content);
}

/**
 * What to send for the turns a host decoded. A turn is `{ role, turnId, content }`
 * with its text in `content`; one that also has tool calls or results carries
 * the host message in `payload`, in a shape the shared part builder reads, and
 * `toolNameById` names the tools those results answer.
 */
export function buildIncrementalCapturePlan(turns, state = {}, cfg = {}, { toolNameById } = {}) {
  const acknowledged = new Set(
    Array.isArray(state.capturedTurnIds) ? state.capturedTurnIds : [],
  );
  const candidates = [];
  for (const turn of turns) {
    if (turn.role === "assistant" && cfg.captureAssistantTurns === false) continue;
    const message = buildCaptureMessage(
      turn.payload || { role: turn.role, content: turn.content },
      turn.role,
      cfg,
      { toolNameById },
    );
    if (!message) continue;
    candidates.push({ dedupKey: turnDedupKey(turn), turn, message });
  }
  const seenInBatch = new Set();
  const toSend = candidates.filter((item) => {
    if (acknowledged.has(item.dedupKey)) return false;
    const batchKey = stableHash(item.dedupKey, JSON.stringify(item.message));
    if (seenInBatch.has(batchKey)) return false;
    seenInBatch.add(batchKey);
    return true;
  });
  const payloads = toSend.map(({ turn, message }) => ({
    ...message,
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
 * The turns `extract` finds past `lastTurnId` in a JSONL transcript, with the
 * tool names their results answer, or a status when the transcript cannot be
 * read. `ok` distinguishes "read it, it has N turns" from "could not read it":
 * a caller must not treat an unreadable transcript as an empty one, or a
 * transient read error would acknowledge turns that were never seen.
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
  // Named over every turn: a result may answer a call in a turn already captured.
  const toolNameById = collectToolNamesByIdFromEntries(turns);
  const lastIndex = lastTurnId == null || lastTurnId === ""
    ? -1
    : turns.findIndex((turn) => turn.turnId === String(lastTurnId));
  return { status: "ok", turns: lastIndex === -1 ? turns : turns.slice(lastIndex + 1), toolNameById };
}

/**
 * The turns a transcript decoder collected as `{ role, recordIndex, blocks }`,
 * blocks in record order. A turn's id is the index of the record that opened
 * it; one with tool blocks carries them as `payload` for the part builder.
 */
export function turnsFromBlocks(collected) {
  const turns = [];
  for (const { role, recordIndex, blocks } of collected) {
    if (blocks.length === 0) continue;
    const turn = {
      role: normalizeCaptureRole(role),
      turnId: String(recordIndex),
      content: blocks.filter((block) => block.type === "text").map((block) => block.text).join("\n\n"),
    };
    if (blocks.some((block) => block.type !== "text")) turn.payload = { role: turn.role, content: blocks };
    turns.push(turn);
  }
  return turns;
}

/**
 * Send the turns a host decoded and commit the session when it is time: at a
 * session end and before a compaction, whatever earlier Stops sent and left
 * uncommitted; on a plain Stop, once the session's pending tokens reach the
 * shared threshold. `endEvents` are the host's events that end a session.
 */
export async function captureIncrementalTurns(ctx, state, event, transcript, {
  cleanText,
  endEvents = ["session-end"],
} = {}) {
  if (transcript.status !== "ok") {
    ctx.log(`capture_${transcript.status}`, transcript.error ? { error: transcript.error } : {});
    return null;
  }
  const boundary = event === "pre-compact" || endEvents.includes(event);
  const commitsAtBoundary = boundary && !(event === "pre-compact" && ctx.cfg.autoCommitOnCompact === false);
  const uncommitted = Number(state.capturedSinceCommit || 0);

  const plan = buildIncrementalCapturePlan(transcript.turns, state, ctx.cfg, {
    toolNameById: transcript.toolNameById,
  });
  let next;
  let sent = 0;
  if (plan.toSend.length > 0) {
    const result = await addAgentMessages(ctx.fetchJSON, ctx.sessionId, plan.payloads, ctx.peerId);
    next = applyIncrementalCaptureResult(state, plan, result, cleanText);
    sent = Number(result?.sent || 0);
  } else if (commitsAtBoundary && uncommitted > 0) {
    // Nothing new to send, but turns sent earlier are still uncommitted, and
    // this may be their last chance.
    next = { ...state, captured: 0 };
  } else {
    return null;
  }

  let capturedSinceCommit = uncommitted + next.captured;
  let due = commitsAtBoundary && capturedSinceCommit > 0;
  if (!boundary && sent > 0) {
    const meta = await getAgentSession(ctx.fetchJSON, ctx.sessionId);
    due = Boolean(meta) && await thresholdCommitDue(ctx.fetchJSON, ctx.sessionId, ctx.cfg, meta);
  }
  if (due) {
    const committed = await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
    if (committed.ok || committed.pendingQueued) capturedSinceCommit = 0;
  }
  const { captured, ...persisted } = next;
  return { ...persisted, capturedSinceCommit };
}
