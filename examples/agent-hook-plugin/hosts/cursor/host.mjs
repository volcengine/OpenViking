import { denyCursorPermission } from "../../lib/uri-guard.mjs";
import { decode, parseCursorTranscript } from "./decode.mjs";

// Cursor prefixes an MCP server's key with where it was configured:
// `user-openviking`, `project-<path>-openviking`, `plugin-<plugin>-openviking`.
const OPENVIKING_MCP_SERVER = /^(?:(?:user|project-.+|plugin-.+)-)?openviking$/;

const TOOL_EVENTS = new Set(["postToolUse", "postToolUseFailure", "afterMCPExecution"]);

const nonEmpty = (value) => typeof value === "string" && value.trim() !== "";
const isObject = (value) => value && typeof value === "object" && !Array.isArray(value);

function mcpCompletion(payload) {
  if (!nonEmpty(payload.mcp_server_name)) return { skip: "missing_mcp_server" };
  let input, result;
  try {
    input = JSON.parse(payload.tool_input);
    result = JSON.parse(payload.result_json);
  } catch {
    return { skip: "invalid_mcp_tool_json" };
  }
  if (!isObject(input) || !isObject(result)) return { skip: "invalid_mcp_tool_payload" };
  const failed = Boolean(result.isError || result.error || result.rejected || result.permissionDenied);
  if (!failed && !Array.isArray(result.content)) return { skip: "missing_mcp_tool_result" };
  // The server recognises its own tools by the `mcp__openviking__` name, so a
  // tool called "read" on another server must keep that server's name.
  const server = OPENVIKING_MCP_SERVER.test(payload.mcp_server_name)
    ? "openviking"
    : payload.mcp_server_name.replace(/^user-/, "");
  return { name: `mcp__${server}__${payload.tool_name}`, input, output: result, failed };
}

/**
 * Cursor's own tools report through postToolUse / postToolUseFailure; MCP calls
 * through afterMCPExecution, the only event that names the server, so the post
 * events skip their `MCP:<tool>` copy.
 */
function completion(event, payload) {
  if (!nonEmpty(payload.tool_use_id) || !nonEmpty(payload.tool_name)) return { skip: "missing_tool_identity" };
  if (event === "afterMCPExecution") return mcpCompletion(payload);
  if (payload.tool_name.startsWith("MCP:")) return { skip: "mcp_tool_copy" };
  if (event === "postToolUseFailure") {
    return {
      name: payload.tool_name,
      input: payload.tool_input,
      output: payload.error_message || payload.failure_type || "",
      failed: true,
    };
  }
  return { name: payload.tool_name, input: payload.tool_input, output: payload.tool_output ?? "", failed: false };
}

/** @type {import("../../lib/flow/host.d.mts").Host} */
export default {
  id: "cursor",
  sessionPrefix: "cu-",
  events: {
    sessionStart: "start",
    beforeSubmitPrompt: "prompt",
    preToolUse: "toolCall",
    postToolUse: "toolResult",
    postToolUseFailure: "toolResult",
    afterMCPExecution: "toolResult",
    stop: "turnEnd",
    preCompact: "compactSoon",
    sessionEnd: "end",
  },
  budgets: {
    sessionStart: 25_000,
    beforeSubmitPrompt: 17_000,
    postToolUse: 2_000,
    postToolUseFailure: 2_000,
    afterMCPExecution: 2_000,
    stop: 25_000,
    preCompact: 25_000,
    sessionEnd: 25_000,
  },
  facts: { historySession: "default" },
  read(event, payload, io) {
    const transcript = String(payload.transcript_path || payload.transcriptPath || "").trim();
    const read = { transcript };
    if (event === "beforeSubmitPrompt") {
      read.prompt = typeof payload.prompt === "string" ? payload.prompt.trim() : "";
      if (nonEmpty(payload.generation_id)) read.eventId = payload.generation_id;
    }
    if (TOOL_EVENTS.has(event)) {
      const done = completion(event, payload);
      if (done.skip) return { ...read, skip: done.skip };
      const text = transcript ? io.text(transcript) : null;
      read.note = {
        afterTurn: text === null ? null : parseCursorTranscript(text).length,
        tool: {
          type: "tool_result",
          tool_use_id: payload.tool_use_id,
          name: done.name,
          state: { input: done.input, output: done.output },
          is_error: done.failed,
        },
      };
    }
    return read;
  },
  decode,
  tool(payload) {
    // beforeShellExecution is no longer installed, but a hooks.json from an
    // older install may still route a shell command here, and it must run.
    if (typeof payload.command === "string") return null;
    // preToolUse names a local MCP tool `MCP:<tool>` without its server.
    if (payload.tool_name === "MCP:session_history") return { history: payload.tool_input };
    if (payload.tool_name !== undefined) return null;
    // beforeReadFile: `{ file_path, content }`.
    return { name: "read", input: payload };
  },
  emit(event, output) {
    if (output.deny) return denyCursorPermission(output.deny);
    if (output.input) return { updated_input: output.input };
    if (event === "beforeSubmitPrompt") {
      return output.context ? { continue: true, additional_context: output.context } : { continue: true };
    }
    if (event === "sessionStart" && output.context) return { additional_context: output.context };
    return {};
  },
};
