import {
  addAgentMessages,
  commitAgentSession,
} from "./shared/agent-hook-runtime.mjs";
import { evaluateUriGuard } from "./shared/uri-guard.mjs";
import {
  applyKimicodeCaptureResult,
  buildKimicodeCapturePlan,
  KIMI_INTERRUPT_REQUEST_TIMEOUT_MS,
  shouldCommitKimicodeCapture,
} from "./kimicode-capture.mjs";
import { buildKimicodeTurns, cleanKimicodeText } from "./kimicode-turns.mjs";

function textFromPromptInput(payload) {
  const input = payload.input;
  if (typeof input === "string") return input;
  if (!Array.isArray(input)) return "";
  return input
    .map((part) => (typeof part === "string" ? part : part?.text || ""))
    .filter(Boolean)
    .join("\n");
}

export const kimicode = {
  prefix: "kc-",
  tracksPendingPrompt: true,
  capturesOnlyWhenEnabled: true,
  detachesCapture: true,
  // Interrupt must remain synchronous so Kimi can finish the cancellation request.
  detachEvents: new Set(["stop", "pre-compact", "session-end"]),
  profileOnSessionStart: false,
  // Leave room in Kimi's 30-second SessionStart hook for state and cleanup.
  sessionStartBudgetMs: 25_000,
  profileOnPrompt: true,
  plainTextEnvelope: true,
  stages: {
    "session-start": "start",
    "user-prompt-submit": "prompt",
    stop: "capture",
    "pre-compact": "capture",
    "session-end": "capture",
    interrupt: "capture",
  },
  envelope(event, block) {
    return event === "user-prompt-submit" ? block || null : null;
  },
  guard(input = {}) {
    const toolName = input.tool_name ?? input.toolName ?? input.name ?? input.tool;
    const toolInput = input.tool_input ?? input.toolInput ?? input.input ?? {};
    const decision = evaluateUriGuard(toolName, toolInput, {
      guarded: new Set(["read", "glob", "grep"]),
    });
    if (!decision) return {};
    return {
      hookSpecificOutput: {
        permissionDecision: "deny",
        permissionDecisionReason: decision.reason,
      },
    };
  },
  normalizeInput(input) {
    return !input.session_id && input.sessionId ? { ...input, session_id: input.sessionId } : input;
  },
  prompt(input) {
    return cleanKimicodeText(
      input.prompt || input.user_prompt || input.user_message || input.message || textFromPromptInput(input) || "",
    );
  },
  defaultTimeoutMs({ cfg, event }) {
    return event === "interrupt"
      ? Math.min(Number(cfg.timeoutMs) || KIMI_INTERRUPT_REQUEST_TIMEOUT_MS, KIMI_INTERRUPT_REQUEST_TIMEOUT_MS)
      : undefined;
  },
  async capture(ctx, state, event) {
    const plan = buildKimicodeCapturePlan(buildKimicodeTurns(ctx.input, state), state, ctx.cfg);
    if (plan.toSend.length === 0) return null;

    const result = await addAgentMessages(ctx.fetchJSON, ctx.sessionId, plan.payloads);
    const { captured, ...nextState } = applyKimicodeCaptureResult(state, plan, result);
    let nextCount = Number(state.capturedSinceCommit || 0) + captured;
    if (shouldCommitKimicodeCapture(event, ctx.cfg, nextCount - captured, captured)) {
      const committed = await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
      if (committed.ok) nextCount = 0;
    }
    return { ...nextState, capturedSinceCommit: nextCount };
  },
};
