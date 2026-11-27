import {
  addAgentMessages,
  commitAgentSession,
  stableHash,
} from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { buildCaptureMessage } from "../../memory-plugin-shared/lib/capture-utils.mjs";
import { preToolUseOutput } from "../../memory-plugin-shared/lib/uri-guard.mjs";
import { buildTraeTurns, cleanTraeText } from "./trae-turns.mjs";

const INJECTION_EVENTS = {
  "session-start": "SessionStart",
  "user-prompt-submit": "UserPromptSubmit",
  "post-compact": "PostCompact",
};

export const trae = {
  prefix: "tr-",
  tracksPendingPrompt: true,
  capturesOnlyWhenEnabled: true,
  requestBudgets: { "session-start": 25_000, "user-prompt-submit": 17_000, "post-compact": 1_000, stop: 25_000 },
  stages: { "session-start": "start", "user-prompt-submit": "prompt", "post-compact": "pointer", stop: "capture" },
  envelope(event, block) {
    const value = { decision: "approve" };
    if (block) {
      value.hookSpecificOutput = {
        hookEventName: INJECTION_EVENTS[event] || "UserPromptSubmit",
        additionalContext: block,
      };
    }
    return value;
  },
  // TRAE holds PostCompact context for the next user prompt.
  historyPointer: {
    sessionDefault: false,
    inject: (event) => (event === "post-compact" ? "compact" : ""),
  },
  // `_meta.chatSessionId` is read from the shipped app, not seen on the wire,
  // so the note above still asks the model to pass `session`.
  historySessionFromMeta: (meta) => (typeof meta.chatSessionId === "string" ? meta.chatSessionId.trim() : ""),
  guard: (input) => preToolUseOutput(input),
  prompt: (input) => cleanTraeText(input.prompt),
  async capture(ctx, state) {
    const hashes = new Set(Array.isArray(state.capturedHashes) ? state.capturedHashes : []);
    const turnKey = state.pendingPrompt?.at || state.lastTurnKey || state.promptHash || "unknown-turn";
    const toSend = [];
    for (const turn of buildTraeTurns(ctx.input, state)) {
      const hash = stableHash(turnKey, turn.role, turn.content);
      if (hashes.has(hash)) continue;
      const message = buildCaptureMessage({ content: turn.content }, turn.role, ctx.cfg);
      if (!message) {
        ctx.log("capture_skip", { role: turn.role, reason: "nothing_to_send" });
        hashes.add(hash);
        continue;
      }
      toSend.push({ hash, message });
    }
    const result = await addAgentMessages(ctx.fetchJSON, ctx.sessionId, toSend.map((item) => item.message), ctx.peerId);
    const captured = result.sent + result.queued;
    for (const item of toSend.slice(0, captured)) hashes.add(item.hash);
    let nextCount = Number(state.capturedSinceCommit || 0) + captured;
    if (captured > 0) {
      const committed = await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
      if (committed.ok) nextCount = 0;
    }
    return {
      ...state,
      capturedHashes: [...hashes].slice(-1000),
      capturedSinceCommit: nextCount,
      pendingPrompt: null,
      lastTurnKey: turnKey,
    };
  },
};

// TRAE CN is the same host with its own configuration key and session prefix.
export const traeCn = { ...trae, prefix: "trcn-" };
