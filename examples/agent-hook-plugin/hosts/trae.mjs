import {
  addAgentMessages,
  commitAgentSession,
  stableHash,
} from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { filterCaptureTurns } from "../../memory-plugin-shared/lib/capture-utils.mjs";
import { denyHookSpecificOutput, evaluateUriGuard } from "../../memory-plugin-shared/lib/uri-guard.mjs";
import { buildTraeTurns, cleanTraeText } from "./trae-turns.mjs";

export const trae = {
  prefix: "tr-",
  tracksPendingPrompt: true,
  capturesOnlyWhenEnabled: true,
  stages: { "session-start": "start", "user-prompt-submit": "prompt", stop: "capture" },
  envelope(event, block) {
    const value = { decision: "approve" };
    if (block) {
      value.hookSpecificOutput = {
        hookEventName: event === "session-start" ? "SessionStart" : "UserPromptSubmit",
        additionalContext: block,
      };
    }
    return value;
  },
  guard(input = {}) {
    const toolName = input.tool_name ?? input.toolName ?? input.name ?? input.tool;
    const toolInput = input.tool_input ?? input.toolInput ?? input.input ?? {};
    const decision = evaluateUriGuard(toolName, toolInput);
    return decision ? denyHookSpecificOutput(decision.reason) : {};
  },
  prompt: (input) => cleanTraeText(input.prompt),
  async capture(ctx, state) {
    const hashes = new Set(Array.isArray(state.capturedHashes) ? state.capturedHashes : []);
    const turnKey = state.pendingPrompt?.at || state.lastTurnKey || state.promptHash || "unknown-turn";
    const toSend = [];
    for (const turn of buildTraeTurns(ctx.input, state)) {
      // Hash the raw turn, not the filtered text, so raising
      // captureMaxLength never resends one the server already holds
      // in truncated form.
      const hash = stableHash(turnKey, turn.role, turn.content);
      if (hashes.has(hash)) continue;
      const { kept, dropped } = filterCaptureTurns([turn], ctx.cfg);
      if (!kept.length) {
        ctx.log("capture_skip", dropped[0]);
        hashes.add(hash);
        continue;
      }
      toSend.push({ hash, turn: kept[0] });
    }
    const result = await addAgentMessages(ctx.fetchJSON, ctx.sessionId, toSend.map((item) => item.turn));
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
