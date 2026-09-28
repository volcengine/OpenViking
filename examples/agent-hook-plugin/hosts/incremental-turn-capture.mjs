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
  const toSend = candidates.filter((item) => !acknowledged.has(item.dedupKey));
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
