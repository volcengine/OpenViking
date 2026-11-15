/**
 * Antigravity adapter (the Antigravity desktop app and the `agy` CLI share it).
 *
 * Antigravity calls PreInvocation before every model call, including each call
 * inside a tool loop, so recall runs only on a turn's first call
 * (`invocationNum` 0). Injected context goes back as a `systemMessage` step,
 * which stays in the conversation. Stop carries no message text: capture reads
 * the finished steps of `transcriptPath` after the last captured step.
 */

import { readFile } from "node:fs/promises";

import { addAgentMessages, commitAgentSession } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { filterCaptureTurns } from "../../memory-plugin-shared/lib/capture-utils.mjs";

// The host wraps a typed request as `<USER_REQUEST>…</USER_REQUEST>` followed by
// metadata blocks. Only that record is unwrapped, and only an envelope that
// opens the record, so text that merely quotes the tags is kept verbatim.
const USER_REQUEST = /^<USER_REQUEST>\s*([\s\S]*)\s*<\/USER_REQUEST>/;
const INJECTED = /<openviking-context\b[^>]*>[\s\S]*?<\/openviking-context>/g;

export function cleanAntigravityText(text) {
  return String(text || "").replace(INJECTED, "").trim();
}

function userRequestText(text) {
  const raw = String(text || "");
  return cleanAntigravityText(raw.match(USER_REQUEST)?.[1] ?? raw);
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
    // A step still being written is read again by the next Stop.
    if (step.status && step.status !== "DONE") break;
    lastStep = index;
    const turn = step.type === "USER_INPUT" && step.source === "USER_EXPLICIT"
      ? { role: "user", content: userRequestText(step.content) }
      : step.type === "PLANNER_RESPONSE" ? { role: "assistant", content: cleanAntigravityText(step.content) } : null;
    if (turn?.content) turns.push(turn);
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
    // One id per user turn, so concurrent or repeated first calls recall once.
    prompt_id: `${input.conversationId}:${input.initialNumSteps}`,
  }),
  prompt: (input) => (Number(input.invocationNum || 0) === 0 ? cleanAntigravityText(input.lastUserInput) : ""),
  async capture(ctx, state) {
    const afterStep = Number.isInteger(state.lastStep) ? state.lastStep : -1;
    const jsonl = ctx.input.transcriptPath ? await readFile(ctx.input.transcriptPath, "utf8").catch(() => "") : "";
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
