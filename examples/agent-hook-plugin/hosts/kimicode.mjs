import { addAgentMessages, commitAgentSession } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { evaluateUriGuard } from "../../memory-plugin-shared/lib/uri-guard.mjs";
import {
  applyIncrementalCaptureResult,
  buildIncrementalCapturePlan,
} from "./incremental-turn-capture.mjs";
import { buildKimicodeTurns, cleanKimicodeText } from "./kimicode-turns.mjs";

export function shouldCommitKimicodeCapture(event, cfg = {}, previous = 0, captured = 0) {
  if (captured <= 0) return false;
  if (event === "interrupt" || event === "session-end") return true;
  if (event === "pre-compact") return cfg.autoCommitOnCompact !== false;
  return Number(previous || 0) + captured >= Number(cfg.commitTurnThreshold || Infinity);
}

function promptParts(input) {
  if (typeof input === "string") return input;
  if (!Array.isArray(input)) return "";
  return input.map((part) => (typeof part === "string" ? part : part?.text || "")).filter(Boolean).join("\n");
}

export const kimicode = {
  prefix: "kc-",
  tracksPendingPrompt: true,
  capturesOnlyWhenEnabled: true,
  detachesCapture: true,
  profileStage: "first-prompt",
  requestBudgets: {
    "session-start": 25_000,
    "user-prompt-submit": 17_000,
    interrupt: 2_000,
  },
  stages: {
    "session-start": "start",
    "user-prompt-submit": "prompt",
    stop: "capture",
    "pre-compact": "capture",
    "session-end": "capture",
    interrupt: "capture-sync",
  },
  envelope(event, block) {
    return event === "user-prompt-submit" ? block || null : null;
  },
  guard(input = {}) {
    const decision = evaluateUriGuard(input.tool_name, input.tool_input || {}, {
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
  prompt(input) {
    return cleanKimicodeText(promptParts(input.prompt));
  },
  async capture(ctx, state, event) {
    const transcript = buildKimicodeTurns(ctx.input, state);
    if (transcript.status !== "ok") {
      ctx.log(`capture_${transcript.status}`, transcript.error ? { error: transcript.error } : {});
      return null;
    }
    const plan = buildIncrementalCapturePlan(transcript.turns, state, ctx.cfg);
    if (plan.toSend.length === 0) return null;

    const result = await addAgentMessages(ctx.fetchJSON, ctx.sessionId, plan.payloads, ctx.peerId);
    const next = applyIncrementalCaptureResult(state, plan, result, cleanKimicodeText);
    let capturedSinceCommit = Number(state.capturedSinceCommit || 0) + next.captured;
    if (shouldCommitKimicodeCapture(event, ctx.cfg, state.capturedSinceCommit, next.captured)) {
      const committed = await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
      if (committed.ok) capturedSinceCommit = 0;
    }
    const { captured, ...persisted } = next;
    return { ...persisted, capturedSinceCommit };
  },
};
