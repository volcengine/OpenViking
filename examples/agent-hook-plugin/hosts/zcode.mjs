/**
 * ZCode adapter.
 *
 * Output contract: ZCode validates hook stdout against a strict JSON schema and
 * silently discards the whole document when it meets a key it does not know, so
 * the envelope never carries a Claude-Code-ism such as `{ "decision": "approve" }`
 * and a pass-through writes nothing at all.
 */

import { addAgentMessages, commitAgentSession } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { denyHookSpecificOutput, evaluateUriGuard } from "../../memory-plugin-shared/lib/uri-guard.mjs";
import { applyZcodeCaptureResult, buildZcodeCapturePlan } from "./zcode-capture.mjs";
import { buildZcodeTurns, cleanZcodeText } from "./zcode-turns.mjs";

export const zcode = {
  prefix: "zc-",
  tracksPendingPrompt: true,
  capturesOnlyWhenEnabled: true,
  detachesCapture: true,
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
  guard(input = {}) {
    const toolName = input.tool_name ?? input.toolName ?? input.name ?? input.tool;
    const toolInput = input.tool_input ?? input.toolInput ?? input.input ?? {};
    const decision = evaluateUriGuard(toolName, toolInput);
    return decision ? denyHookSpecificOutput(decision.reason) : {};
  },
  // ZCode may pass sessionId in camelCase or snake_case. Ensure both are present
  // so resolveNativeSessionId() finds it via the direct lookup path — avoids the
  // cwd fallback that would collide for two windows in the same directory.
  normalizeInput: (input) => (
    !input.session_id && input.sessionId ? { ...input, session_id: input.sessionId } : input
  ),
  prompt: (input) => cleanZcodeText(
    input.prompt || input.user_prompt || input.userMessage || input.user_message || input.message || "",
  ),
  async capture(ctx, state) {
    const plan = buildZcodeCapturePlan(buildZcodeTurns(ctx.input, state), state, ctx.cfg);
    // Fail-closed: if no turns and no dedup keys, skip silently (not an error —
    // could be a Stop with no new content, or a race with UserPromptSubmit).
    if (plan.toSend.length === 0) return null;

    const result = await addAgentMessages(ctx.fetchJSON, ctx.sessionId, plan.payloads);
    const { captured, ...nextState } = applyZcodeCaptureResult(state, plan, result);
    let nextCount = Number(state.capturedSinceCommit || 0) + captured;
    if (captured > 0) {
      const committed = await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
      if (committed.ok) nextCount = 0;
    }
    return { ...nextState, capturedSinceCommit: nextCount };
  },
};
