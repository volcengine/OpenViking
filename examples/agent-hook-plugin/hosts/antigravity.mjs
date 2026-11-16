/**
 * Antigravity adapter (the Antigravity desktop app and the `agy` CLI share it).
 *
 * Antigravity calls PreInvocation before every model call, including each call
 * inside a tool loop (`invocationNum` > 0) and each reactive wakeup for a
 * background task, subagent message, or Stop continuation (`invocationNum` 0
 * with a higher `initialNumSteps`). Recall runs only when `invocationNum` is 0
 * and no `MODEL` step has run since the latest `USER_INPUT` step in
 * `transcriptPath`, keyed by that user step so a turn injects once. When the
 * new turn's user step is not in the transcript yet, `lastUserInput` differing
 * from the last recorded request marks it as a new turn. Injected
 * context goes back as a `systemMessage` step, which stays in the conversation.
 * Stop carries no message text: capture reads the finished steps of
 * `transcriptPath` after the last captured step.
 */

import { existsSync, readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { basename, dirname, join } from "node:path";

import { addAgentMessages, commitAgentSession } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { filterCaptureTurns } from "../../memory-plugin-shared/lib/capture-utils.mjs";

// The host wraps a typed request as `<USER_REQUEST>…</USER_REQUEST>` followed by
// metadata blocks. Only that record is unwrapped, and only an envelope that
// opens the record, so text that merely quotes the tags is kept verbatim.
const USER_REQUEST = /^<USER_REQUEST>\s*([\s\S]*)\s*<\/USER_REQUEST>/;
const INJECTED = /<openviking-context\b[^>]*>[\s\S]*?<\/openviking-context>/g;
const IN_PROGRESS_STATUSES = new Set(["PENDING", "QUEUED", "WAITING", "RUNNING", "GENERATING"]);

export function cleanAntigravityText(text) {
  return String(text || "").replace(INJECTED, "").trim();
}

function userRequestText(text) {
  const raw = String(text || "");
  return cleanAntigravityText(raw.match(USER_REQUEST)?.[1] ?? raw);
}

const sameText = (text) => userRequestText(text).replace(/\s+/g, " ");

/**
 * The host may hand over `transcript.jsonl`, whose step contents are cut short;
 * the untruncated `transcript_full.jsonl` beside it is read instead when present.
 */
export function antigravityTranscriptPath(transcriptPath) {
  if (!transcriptPath) return "";
  if (basename(transcriptPath) !== "transcript.jsonl") return transcriptPath;
  const full = join(dirname(transcriptPath), "transcript_full.jsonl");
  return existsSync(full) ? full : transcriptPath;
}

/**
 * Return `step_index + 1` of the unanswered `USER_INPUT` step before
 * `initialNumSteps`, `null` when a `MODEL` step has already run since the last
 * user step (a background-task, subagent, or Stop wakeup), or `undefined` when
 * the transcript has no parsed steps yet.
 *
 * With `lastUserInput`, a `null` becomes `undefined` when that input differs
 * from the last user step's text: the host has not written the new turn's
 * `USER_INPUT` step yet, so this is a new turn, not a wakeup.
 */
export function resolveAntigravityTurnStep(jsonl, initialNumSteps, lastUserInput) {
  const limit = Number.isInteger(initialNumSteps) && initialNumSteps > 0 ? initialNumSteps : Infinity;
  let lastUserStep = -1;
  let lastUserText = "";
  let modelRanSinceUser = false;
  let sawStep = false;
  for (const line of String(jsonl || "").split("\n")) {
    if (!line.trim()) continue;
    let step;
    try { step = JSON.parse(line); } catch { break; }
    const index = Number(step.step_index);
    if (!Number.isInteger(index)) continue;
    if (index >= limit) break;
    sawStep = true;
    if (step.type === "USER_INPUT" && step.source === "USER_EXPLICIT") {
      lastUserStep = index;
      // A truncated record (transcript.jsonl keeps only the head and tail) can
      // not be compared, so it never marks a new turn.
      lastUserText = Array.isArray(step.truncated_fields) && step.truncated_fields.includes("content")
        ? null
        : sameText(step.content);
      modelRanSinceUser = false;
    } else if (lastUserStep >= 0 && step.source === "MODEL") {
      modelRanSinceUser = true;
    }
  }
  if (!sawStep) return undefined;
  if (lastUserStep < 0 || modelRanSinceUser) {
    const pending = sameText(lastUserInput);
    return pending && lastUserText !== null && pending !== lastUserText ? undefined : null;
  }
  return lastUserStep + 1;
}

/** The user requests and text replies of finished steps after `afterStep`. */
export function buildAntigravityTurns(jsonl, afterStep = -1) {
  const turns = [];
  let lastStep = afterStep;
  for (const line of String(jsonl || "").split("\n")) {
    if (!line.trim()) continue;
    let step;
    try { step = JSON.parse(line); } catch { break; }
    const index = Number(step.step_index);
    if (!(index > afterStep)) continue;
    const isUser = step.type === "USER_INPUT" && step.source === "USER_EXPLICIT";
    const isAssistant = step.type === "PLANNER_RESPONSE";
    // Backgrounded commands (`GENERIC` with `RUNNING`) and failed tool steps
    // stay non-DONE permanently and are never captured, so they must not stall
    // the cursor.
    if (!isUser && !isAssistant) {
      lastStep = index;
      continue;
    }
    // An in-flight turn step is read again by the next Stop; terminal failures
    // (CANCELED, ERROR, INVALID, …) will never become DONE and are skipped.
    if (IN_PROGRESS_STATUSES.has(step.status)) break;
    lastStep = index;
    if (step.status && step.status !== "DONE") continue;
    const turn = isUser
      ? { role: "user", content: userRequestText(step.content) }
      : { role: "assistant", content: cleanAntigravityText(step.content) };
    if (turn.content) turns.push(turn);
  }
  return { turns, lastStep };
}

export const antigravity = {
  prefix: "ag-",
  capturesOnlyWhenEnabled: true,
  detachesCapture: true,
  requestBudgets: { "session-start": 25_000, "pre-invocation": 17_000, stop: 25_000 },
  stages: { "session-start": "start", "pre-invocation": "prompt", stop: "capture" },
  envelope(event, block) {
    return block ? { injectSteps: [{ systemMessage: { systemMessage: block } }] } : {};
  },
  normalizeInput: (input) => ({
    ...input,
    session_id: input.conversationId,
  }),
  prompt(input) {
    if (Number(input.invocationNum || 0) !== 0) return "";
    const text = cleanAntigravityText(input.lastUserInput);
    if (!text) return "";
    let turnStep;
    if (input.transcriptPath) {
      try {
        turnStep = resolveAntigravityTurnStep(
          readFileSync(antigravityTranscriptPath(input.transcriptPath), "utf8"), input.initialNumSteps, text);
      } catch {
        turnStep = undefined;
      }
    }
    if (turnStep === null) return "";
    // Key by the user step rather than initialNumSteps, which advances on wakeups.
    input.prompt_id = `${input.conversationId}:${turnStep ?? input.initialNumSteps}`;
    return text;
  },
  async capture(ctx, state) {
    const afterStep = Number.isInteger(state.lastStep) ? state.lastStep : -1;
    const path = antigravityTranscriptPath(ctx.input.transcriptPath);
    const jsonl = path ? await readFile(path, "utf8").catch(() => "") : "";
    const { turns, lastStep } = buildAntigravityTurns(jsonl, afterStep);
    const { kept, dropped } = filterCaptureTurns(turns, ctx.cfg);
    for (const item of dropped) ctx.log("capture_skip", item);
    const result = kept.length
      ? await addAgentMessages(ctx.fetchJSON, ctx.sessionId, kept, ctx.peerId)
      : { sent: 0, queued: 0 };
    const captured = result.sent + result.queued;
    if (captured > 0) await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
    // Keep the cursor when a write was lost, so the next Stop resends it.
    return { ...state, lastStep: captured >= kept.length ? lastStep : afterStep };
  },
};
