import {
  DEFAULT_TOOL_HINTS,
  denyHookSpecificOutput,
  noticeHookSpecificOutput,
  readToolEvent,
} from "../../lib/uri-guard.mjs";
import { decode } from "./decode.mjs";

// MCP tools load lazily by default, so the model reaches them only through
// `mcp_call` (DESIGN.md §9); the deny reason names that call.
function viaMcpCall(example) {
  const match = /^(\w+)\((.*)\)$/s.exec(example || "");
  return match ? `mcp_call(toolName="mcp__openviking__${match[1]}", arguments={${match[2]}})` : example;
}

const toolHints = Object.fromEntries(
  ["read", "glob", "grep", "edit", "write", "bash"].map((name) => {
    const { tool, example } = DEFAULT_TOOL_HINTS[name];
    return [name, { tool, example: (uri, input) => viaMcpCall(example(uri, input)) }];
  }),
);

const HISTORY_TOOL = "mcp__openviking__session_history";

const INJECTS = { "session-start": "SessionStart", "user-prompt-submit": "UserPromptSubmit" };

/** @type {import("../../lib/flow/host.d.mts").Host} */
export default {
  id: "qoder",
  sessionPrefix: "qd-",
  events: {
    "session-start": "start",
    "user-prompt-submit": "prompt",
    "pre-tool-use": "toolCall",
    stop: "turnEnd",
    "pre-compact": "compactSoon",
    "session-end": "end",
  },
  budgets: {
    "session-start": 25_000,
    "user-prompt-submit": 17_000,
    stop: 25_000,
    "pre-compact": 25_000,
    "session-end": 25_000,
  },
  canDetach: true,
  facts: {
    historyTool: `the OpenViking MCP tool session_history (mcp_call toolName="${HISTORY_TOOL}")`,
  },
  toolHints,
  read(event, payload) {
    const { source } = payload;
    return {
      prompt: typeof payload.prompt === "string" ? payload.prompt.trim() : "",
      // The auxiliary session `qodercli -p` ends with names a transcript that
      // never exists (DESIGN.md §3); capture logs it and moves on.
      transcript: String(payload.transcript_path || payload.transcriptPath || "").trim(),
      // SessionStart(compact) and SessionStart(resume) reach the model.
      resumed: event === "session-start" && (source === "compact" || source === "resume") ? source : "",
    };
  },
  decode,
  // Lazy MCP loading routes session_history through `mcp_call`; the direct
  // name is what `mcp.lazyLoad: false` shows (DESIGN.md §9).
  tool(payload) {
    const { toolName, toolInput } = readToolEvent(payload);
    if (toolName === "mcp_call" && toolInput?.toolName === HISTORY_TOOL) return { history: toolInput.arguments ?? {} };
    if (toolName === HISTORY_TOOL) return { history: toolInput };
    return { name: toolName, input: toolInput };
  },
  emit(event, output, payload) {
    if (output.deny) return denyHookSpecificOutput(output.deny);
    if (output.notice) return noticeHookSpecificOutput(output.notice);
    if (output.input) {
      // No permissionDecision, so the host still asks before the call runs.
      const updatedInput = payload.tool_name === "mcp_call"
        ? { ...payload.tool_input, arguments: output.input }
        : output.input;
      return { hookSpecificOutput: { hookEventName: "PreToolUse", updatedInput } };
    }
    // Qoder's top-level `decision` only takes allow/deny, so an injection
    // carries `hookSpecificOutput` alone (DESIGN.md §5).
    const hookEventName = INJECTS[event];
    return hookEventName && output.context
      ? { hookSpecificOutput: { hookEventName, additionalContext: output.context } }
      : null;
  },
};
