import { commitAgentSession } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { parseCursorTranscript } from "./cursor-transcript.mjs";
import { captureTranscript } from "./transcript-capture.mjs";

/** Qoder CLI uses Claude-style hook payloads and transcript messages. */
export const qoder = {
  prefix: "qd-",
  capturesOnlyWhenEnabled: true,
  requestBudgets: { "session-start": 25_000, "user-prompt-submit": 17_000, stop: 25_000 },
  stages: { "session-start": "start", "user-prompt-submit": "prompt", stop: "capture" },
  envelope(event, block) {
    if (!block || event === "stop") return {};
    return {
      hookSpecificOutput: {
        hookEventName: event === "session-start" ? "SessionStart" : "UserPromptSubmit",
        additionalContext: block,
      },
    };
  },
  prompt: (input) => (typeof input.prompt === "string" ? input.prompt.trim() : ""),
  async capture(ctx, state) {
    const { state: next, captured } = await captureTranscript(ctx, state, parseCursorTranscript);
    if (captured > 0) {
      const result = await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
      if (result.ok) next.capturedSinceCommit = 0;
    }
    return next;
  },
};
