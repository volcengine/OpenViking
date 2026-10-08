import {
  commitAgentSession,
} from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { denyCursorPermission, evaluateUriGuard } from "../../memory-plugin-shared/lib/uri-guard.mjs";
import { parseCursorTranscript } from "./cursor-transcript.mjs";
import { captureTranscript } from "./transcript-capture.mjs";

export const cursor = {
  prefix: "cu-",
  requestBudgets: {
    sessionStart: 25_000,
    beforeSubmitPrompt: 17_000,
    stop: 25_000,
    preCompact: 25_000,
    sessionEnd: 25_000,
  },
  stages: {
    sessionStart: "start",
    beforeSubmitPrompt: "prompt",
    stop: "capture",
    preCompact: "capture",
    sessionEnd: "capture",
  },
  envelope(event, block) {
    if (event === "beforeSubmitPrompt") {
      return block ? { continue: true, additional_context: block } : { continue: true };
    }
    if (event === "sessionStart" && block) return { additional_context: block };
    return {};
  },
  guard(input = {}) {
    // beforeShellExecution is no longer installed, but a hooks.json from an
    // older install may still route a shell command here, and it must run.
    if (typeof input.command === "string") return {};
    const decision = evaluateUriGuard("read", input);
    return decision ? denyCursorPermission(decision.reason) : {};
  },
  prompt: (input) => (typeof input.prompt === "string" ? input.prompt.trim() : ""),
  async capture(ctx, state, event) {
    const { state: next } = await captureTranscript(ctx, state, parseCursorTranscript);
    // Only a plain Stop waits for the threshold: a compaction or a session end
    // is the last chance this transcript has to reach the server.
    const shouldCommit = event !== "stop" || next.capturedSinceCommit >= ctx.cfg.commitTurnThreshold;
    if (shouldCommit) {
      const result = await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
      if (result.ok) next.capturedSinceCommit = 0;
    }
    return next;
  },
};
