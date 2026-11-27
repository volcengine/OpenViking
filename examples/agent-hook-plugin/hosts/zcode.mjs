/**
 * ZCode adapter.
 *
 * Output contract: ZCode validates hook stdout against a strict JSON schema and
 * silently discards the whole document when it meets a key it does not know, so
 * the envelope never carries a Claude-Code-ism such as `{ "decision": "approve" }`
 * and a pass-through writes nothing at all.
 */

import { sanitizeCapturedText } from "../../memory-plugin-shared/lib/capture-utils.mjs";
import { preToolUseOutput } from "../../memory-plugin-shared/lib/uri-guard.mjs";
import { captureIncrementalTurns } from "./incremental-turn-capture.mjs";
import { buildZcodeTurns } from "./zcode-turns.mjs";

export const zcode = {
  prefix: "zc-",
  tracksPendingPrompt: true,
  capturesOnlyWhenEnabled: true,
  detachesCapture: true,
  requestBudgets: { "session-start": 25_000, "user-prompt-submit": 17_000, stop: 25_000 },
  stages: { "session-start": "start", "user-prompt-submit": "prompt", stop: "capture" },
  envelope(event, block) {
    if (!block) return null;
    return {
      hookSpecificOutput: {
        hookEventName: event === "session-start" ? "SessionStart" : "UserPromptSubmit",
        additionalContext: block,
      },
    };
  },
  // The matcher names no shell tool, so the notice never fires: ZCode's strict
  // schema is not verified to accept additionalContext on PreToolUse.
  guard: (input) => preToolUseOutput(input),
  // ZCode may pass sessionId in camelCase or snake_case. Ensure both are present
  // so resolveNativeSessionId() finds it via the direct lookup path — avoids the
  // cwd fallback that would collide for two windows in the same directory.
  normalizeInput: (input) => (
    !input.session_id && input.sessionId ? { ...input, session_id: input.sessionId } : input
  ),
  prompt: (input) => sanitizeCapturedText(
    input.prompt || input.user_prompt || input.userMessage || input.user_message || input.message || "",
  ),
  // ZCode has no session-end or compaction event, so every Stop commits what
  // it and earlier Stops left uncommitted.
  async capture(ctx, state, event) {
    return captureIncrementalTurns(ctx, state, event, buildZcodeTurns(ctx.input, state), {
      cleanText: sanitizeCapturedText,
      endEvents: ["stop"],
    });
  },
};
