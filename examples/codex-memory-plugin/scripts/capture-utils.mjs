import {
  extractCaptureTurns as extractSharedCaptureTurns,
} from "./shared/capture-utils.mjs";

export * from "./shared/capture-utils.mjs";

function mcpResultText(result) {
  const ok = result?.Ok;
  if (Array.isArray(ok?.content)) {
    const text = ok.content
      .filter((part) => part?.type === "text" && typeof part.text === "string")
      .map((part) => part.text)
      .join("\n");
    if (text) return text;
  }
  if (typeof ok === "string") return ok;
  if (ok != null) return JSON.stringify(ok);
  const error = result?.Err;
  if (typeof error === "string") return error;
  return error == null ? "" : JSON.stringify(error);
}

function normalizeCodexMcpToolEvents(rolloutEntries) {
  return (rolloutEntries || []).flatMap((entry) => {
    const payload = entry?.payload;
    if (payload?.type !== "mcp_tool_call_end") return [entry];

    const callId = payload.call_id;
    const toolName = payload.invocation?.tool;
    if (!callId || !toolName) return [entry];

    const call = {
      payload: {
        type: "function_call",
        id: callId,
        name: toolName,
        arguments: payload.invocation?.arguments || {},
      },
    };
    const output = mcpResultText(payload.result);
    const completed = Object.prototype.hasOwnProperty.call(payload.result || {}, "Ok");
    const result = {
      payload: {
        type: "function_call_output",
        call_id: callId,
        ...(completed ? { output } : { error: output || "MCP tool call failed" }),
      },
    };
    return [call, result];
  });
}

export function extractCaptureTurns(rolloutEntries, cfg = {}) {
  return extractSharedCaptureTurns(normalizeCodexMcpToolEvents(rolloutEntries), cfg);
}
