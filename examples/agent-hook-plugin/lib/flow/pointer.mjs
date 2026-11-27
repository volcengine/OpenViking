/**
 * What a compaction or a resume leaves for the model: the note that says
 * OpenViking holds the full session, and, on a host whose compaction drops the
 * profile the session started with, the profile again. An event whose answer
 * reaches the model delivers them; any other leaves them in the session state
 * for the next prompt.
 */

import { isCaptureEnabled } from "../capture-utils.mjs";
import { formatSessionHistoryPointer } from "../session-history-pointer.mjs";

/** "compact", "resume" or "": what this event says happened to the context. */
export function contextEvent(ctx) {
  if (ctx.stage === "compactSoon") return "compact";
  return ctx.read.resumed || "";
}

/** Whether what this event answers reaches the model. */
export function reachesModel(ctx) {
  if (ctx.stage === "prompt") return true;
  if (ctx.read.defer) return false;
  if (ctx.stage === "start") return ctx.host.facts?.profileAt !== "firstPrompt";
  return ctx.stage === "compacted";
}

function pointerEnabled(ctx) {
  return Boolean(ctx.cfg.sessionHistoryPointer) && isCaptureEnabled(ctx.cfg);
}

// A resumed transcript still holds the note sent before, so a resume brings one
// only when none was sent or is pending; a pending compaction note stays.
function pointerFor(state, kind) {
  return kind === "resume" && (state.historyPointerSent || state.historyPointer) ? "" : kind;
}

export function formatPointer(ctx, kind) {
  if (!kind) return "";
  const { historySession = "default", historyTool } = ctx.host.facts || {};
  return formatSessionHistoryPointer(ctx.sessionId, {
    event: kind,
    sessionDefault: historySession === "default",
    ...(historyTool ? { tool: historyTool } : {}),
  });
}

/** The state after an event that cannot reach the model: what it owes waits. */
export function deferContext(ctx, state) {
  const kind = contextEvent(ctx);
  if (!kind) return state;
  let next = state;
  const pointer = pointerEnabled(ctx) ? pointerFor(state, kind) : "";
  if (pointer) next = { ...next, historyPointer: pointer };
  if (kind === "compact" && ctx.host.facts?.compactionDropsProfile) next = { ...next, profileStale: true };
  return next;
}

/** The note this event delivers, its own or one left waiting, and the state once it has. */
export function deliverPointer(ctx, state) {
  const kind = pointerEnabled(ctx) ? pointerFor(state, contextEvent(ctx)) || state.historyPointer || "" : "";
  if (!kind) return { pointer: "", state };
  return {
    pointer: formatPointer(ctx, kind),
    state: { ...state, historyPointer: undefined, historyPointerSent: true },
  };
}
