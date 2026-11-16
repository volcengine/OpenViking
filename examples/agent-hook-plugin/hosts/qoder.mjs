import { addAgentMessages, commitAgentSession } from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { DEFAULT_TOOL_HINTS, preToolUseOutput } from "../../memory-plugin-shared/lib/uri-guard.mjs";
import {
  applyIncrementalCaptureResult,
  buildIncrementalCapturePlan,
  shouldCommitCapture,
} from "./incremental-turn-capture.mjs";
import { buildQoderTurns, cleanQoderText } from "./qoder-turns.mjs";

export { shouldCommitCapture as shouldCommitQoderCapture };

// MCP tools load lazily by default, so the model reaches them only through
// `mcp_call` (DESIGN.md §9); the deny reason names that call.
function viaMcpCall(example) {
  const match = /^(\w+)\((.*)\)$/s.exec(example || "");
  return match ? `mcp_call(toolName="mcp__openviking__${match[1]}", arguments={${match[2]}})` : example;
}

const QODER_TOOL_HINTS = Object.fromEntries(
  ["read", "glob", "grep", "edit", "write"].map((name) => {
    const { tool, example } = DEFAULT_TOOL_HINTS[name];
    return [name, { tool, example: (uri, input) => viaMcpCall(example(uri, input)) }];
  }),
);

const INJECTION_EVENTS = {
  "session-start": "SessionStart",
  "user-prompt-submit": "UserPromptSubmit",
};

export const qoder = {
  prefix: "qd-",
  tracksPendingPrompt: true,
  capturesOnlyWhenEnabled: true,
  detachesCapture: true,
  profileStage: "session-start",
  requestBudgets: {
    "session-start": 25_000,
    "user-prompt-submit": 17_000,
    stop: 25_000,
    "pre-compact": 25_000,
    "session-end": 25_000,
  },
  stages: {
    "session-start": "start",
    "user-prompt-submit": "prompt",
    stop: "capture",
    "pre-compact": "capture",
    "session-end": "capture",
  },
  envelope(event, block) {
    // Qoder's top-level `decision` only takes allow/deny, so the envelope
    // carries `hookSpecificOutput` alone (DESIGN.md §5).
    const hookEventName = INJECTION_EVENTS[event];
    if (!hookEventName || !block) return null;
    return { hookSpecificOutput: { hookEventName, additionalContext: block } };
  },
  guard(input = {}) {
    return preToolUseOutput(input, { hints: QODER_TOOL_HINTS });
  },
  prompt(input) {
    return typeof input.prompt === "string" ? input.prompt.trim() : "";
  },
  async capture(ctx, state, event) {
    const transcript = buildQoderTurns(ctx.input, state, ctx.cfg);
    if (transcript.status !== "ok") {
      // Includes the auxiliary session `qodercli -p` ends with, whose
      // transcript never exists (DESIGN.md §3).
      ctx.log(`capture_${transcript.status}`, transcript.error ? { error: transcript.error } : {});
      return null;
    }
    const plan = buildIncrementalCapturePlan(transcript.turns, state, ctx.cfg);
    let next;
    if (plan.toSend.length > 0) {
      const result = await addAgentMessages(ctx.fetchJSON, ctx.sessionId, plan.payloads, ctx.peerId);
      next = applyIncrementalCaptureResult(state, plan, result, cleanQoderText);
    } else if (shouldCommitCapture(event, ctx.cfg, state.capturedSinceCommit, 0)) {
      next = { ...state, captured: 0 };
    } else {
      return null;
    }
    let capturedSinceCommit = Number(state.capturedSinceCommit || 0) + next.captured;
    if (shouldCommitCapture(event, ctx.cfg, state.capturedSinceCommit, next.captured)) {
      const committed = await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
      if (committed.ok || committed.pendingQueued) capturedSinceCommit = 0;
    }
    const { captured, ...persisted } = next;
    return { ...persisted, capturedSinceCommit };
  },
};
