import {
  applyIncrementalCaptureResult,
  buildIncrementalCapturePlan,
  turnDedupKey,
} from "./incremental-turn-capture.mjs";
import { cleanZcodeText } from "./zcode-turns.mjs";

export const zcodeTurnDedupKey = turnDedupKey;

export function buildZcodeCapturePlan(turns, state = {}, cfg = {}) {
  return buildIncrementalCapturePlan(turns, state, cfg);
}

export function applyZcodeCaptureResult(state, plan, result) {
  return applyIncrementalCaptureResult(state, plan, result, cleanZcodeText);
}
