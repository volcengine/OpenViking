import { DEFAULT_TOOL_HINTS, preToolUseOutput } from "../../memory-plugin-shared/lib/uri-guard.mjs";
import { captureIncrementalTurns } from "./incremental-turn-capture.mjs";
import { withoutHistorySession } from "./history-session.mjs";
import { buildQoderTurns, cleanQoderText } from "./qoder-turns.mjs";

// MCP tools load lazily by default, so the model reaches them only through
// `mcp_call` (DESIGN.md §9); the deny reason names that call.
function viaMcpCall(example) {
  const match = /^(\w+)\((.*)\)$/s.exec(example || "");
  return match ? `mcp_call(toolName="mcp__openviking__${match[1]}", arguments={${match[2]}})` : example;
}

const QODER_TOOL_HINTS = Object.fromEntries(
  ["read", "glob", "grep", "edit", "write", "bash"].map((name) => {
    const { tool, example } = DEFAULT_TOOL_HINTS[name];
    return [name, { tool, example: (uri, input) => viaMcpCall(example(uri, input)) }];
  }),
);

const HISTORY_TOOL = "mcp__openviking__session_history";

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
    "pre-tool-use": 1_000,
    stop: 25_000,
    "pre-compact": 25_000,
    "session-end": 25_000,
  },
  stages: {
    "session-start": "start",
    "user-prompt-submit": "prompt",
    "pre-tool-use": "fill",
    stop: "capture",
    "pre-compact": "capture",
    "session-end": "capture",
  },
  envelope(event, block) {
    // Qoder's top-level `decision` only takes allow/deny, so the envelope
    // carries `hookSpecificOutput` alone (DESIGN.md §5).
    if (event === "pre-tool-use") return block || null;
    const hookEventName = INJECTION_EVENTS[event];
    if (!hookEventName || !block) return null;
    return { hookSpecificOutput: { hookEventName, additionalContext: block } };
  },
  historyPointer: {
    sessionDefault: true,
    tool: `the OpenViking MCP tool session_history (mcp_call toolName="${HISTORY_TOOL}")`,
    inject: (event, input) => (
      event === "session-start" && (input.source === "compact" || input.source === "resume") ? input.source : ""
    ),
  },
  // Lazy MCP loading routes the call through `mcp_call`; the direct name is
  // what `mcp.lazyLoad: false` shows. No permissionDecision, so the host still
  // asks before the call runs (DESIGN.md §9).
  fillHistorySession(input, session) {
    const toolInput = input.tool_input;
    let updatedInput = null;
    if (input.tool_name === "mcp_call" && toolInput?.toolName === HISTORY_TOOL) {
      const args = withoutHistorySession(toolInput.arguments ?? {});
      if (args) updatedInput = { ...toolInput, arguments: { ...args, session } };
    } else if (input.tool_name === HISTORY_TOOL) {
      const args = withoutHistorySession(toolInput ?? {});
      if (args) updatedInput = { ...args, session };
    }
    return updatedInput ? { hookSpecificOutput: { hookEventName: "PreToolUse", updatedInput } } : null;
  },
  guard(input = {}) {
    return preToolUseOutput(input, { hints: QODER_TOOL_HINTS });
  },
  prompt(input) {
    return typeof input.prompt === "string" ? input.prompt.trim() : "";
  },
  async capture(ctx, state, event) {
    return captureIncrementalTurns(ctx, state, event, buildQoderTurns(ctx.input, state), { cleanText: cleanQoderText });
  },
};
