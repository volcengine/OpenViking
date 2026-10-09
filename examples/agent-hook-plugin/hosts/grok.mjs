import {
  addAgentMessages,
  commitAgentSession,
  stableHash,
} from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { filterCaptureTurns } from "../../memory-plugin-shared/lib/capture-utils.mjs";

function cleanGrokText(value) {
  return String(value || "")
    .replace(/<openviking-context\b[^>]*>[\s\S]*?<\/openviking-context>/giu, "")
    .replace(/<relevant-memories>[\s\S]*?<\/relevant-memories>/giu, "")
    .trim();
}

function buildGrokTurns(input = {}, state = {}) {
  return [
    { role: "user", content: cleanGrokText(input.prompt || state.pendingPrompt?.prompt) },
    {
      role: "assistant",
      content: cleanGrokText(input.lastAssistantMessage || input.last_assistant_message),
    },
  ].filter((turn) => turn.content);
}

export const grok = {
  prefix: "gr-",
  profileStage: "first-prompt",
  tracksPendingPrompt: true,
  defersPromptContext: true,
  capturesOnlyWhenEnabled: true,
  requestBudgets: {
    "session-start": 25_000,
    "user-prompt-submit": 17_000,
    "post-tool-use": 5_000,
    "post-tool-use-failure": 5_000,
    stop: 25_000,
  },
  stages: {
    "session-start": "start",
    "user-prompt-submit": "prompt",
    "post-tool-use": "deliver",
    "post-tool-use-failure": "deliver",
    stop: "capture",
  },
  envelope(event, block) {
    if (!block || (event !== "post-tool-use" && event !== "post-tool-use-failure")) return null;
    return {
      hookSpecificOutput: {
        hookEventName: event === "post-tool-use" ? "PostToolUse" : "PostToolUseFailure",
        additionalContext: block,
      },
    };
  },
  prompt: (input) => cleanGrokText(input.prompt),
  async capture(ctx, state) {
    const hashes = new Set(Array.isArray(state.capturedHashes) ? state.capturedHashes : []);
    const turnKey = ctx.input.promptId || state.pendingPrompt?.at || state.promptHash || "unknown-turn";
    const toSend = [];
    for (const turn of buildGrokTurns(ctx.input, state)) {
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
    const result = await addAgentMessages(
      ctx.fetchJSON,
      ctx.sessionId,
      toSend.map((item) => item.turn),
      ctx.peerId,
    );
    const captured = result.sent + result.queued;
    for (const item of toSend.slice(0, captured)) hashes.add(item.hash);
    if (captured > 0) await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
    return {
      ...state,
      capturedHashes: [...hashes].slice(-1000),
      pendingPrompt: null,
      lastTurnKey: turnKey,
    };
  },
};
