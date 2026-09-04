import { stableHash } from "./shared/agent-hook-runtime.mjs";
import { shouldCaptureText } from "./shared/capture-utils.mjs";
import { cleanZcodeText } from "./zcode-turns.mjs";

export function zcodeTurnDedupKey(turn) {
  return turn.turnId
    ? `${turn.turnId}:${turn.role}`
    : stableHash(turn.role, turn.content);
}

export function buildZcodeCapturePlan(turns, state = {}, cfg = {}) {
  const capturedTurnIds = new Set(
    Array.isArray(state.capturedTurnIds) ? state.capturedTurnIds : [],
  );
  const candidates = [];
  for (const turn of turns) {
    const decision = shouldCaptureText(turn.content, turn.role, cfg);
    if (!decision.shouldCapture) continue;
    // The dedup key stays keyed on the raw turn so raising captureMaxLength
    // never resends a turn the server already holds in truncated form.
    candidates.push({ dedupKey: zcodeTurnDedupKey(turn), turn, content: decision.text });
  }
  const toSend = candidates.filter((item) => !capturedTurnIds.has(item.dedupKey));
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

export function applyZcodeCaptureResult(state, plan, result) {
  const acknowledged = new Set(
    Array.isArray(state.capturedTurnIds) ? state.capturedTurnIds : [],
  );
  const captured = Math.min(
    plan.toSend.length,
    Math.max(0, Number(result?.sent || 0) + Number(result?.queued || 0)),
  );
  const newlyAcknowledged = plan.toSend.slice(0, captured);
  for (const item of newlyAcknowledged) acknowledged.add(item.dedupKey);

  const cursor = acknowledgedCursor(plan.candidates, acknowledged);
  const pendingPrompt = cleanZcodeText(state.pendingPrompt?.prompt || "");
  let pendingPromptItem = null;
  if (pendingPrompt) {
    for (let index = plan.candidates.length - 1; index >= 0; index--) {
      const item = plan.candidates[index];
      if (item.turn.role === "user" && cleanZcodeText(item.turn.content) === pendingPrompt) {
        pendingPromptItem = item;
        break;
      }
    }
  }
  const pendingPromptAcknowledged = pendingPromptItem
    ? acknowledged.has(pendingPromptItem.dedupKey)
    : false;

  return {
    ...state,
    capturedTurnIds: [...acknowledged].slice(-1000),
    pendingPrompt: pendingPromptAcknowledged ? null : state.pendingPrompt,
    lastTurnId: cursor || state.lastTurnId || null,
    captured,
  };
}
