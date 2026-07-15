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
    const completed = (
      Object.prototype.hasOwnProperty.call(payload.result || {}, "Ok")
      && payload.result?.Ok?.isError !== true
    );
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

function parseJsonObject(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function collectExperienceUsageMetadata(rolloutEntries) {
  const metadata = new Map();
  for (const entry of rolloutEntries || []) {
    const payload = entry?.payload;
    if (payload?.type !== "mcp_tool_call_end" || !payload.call_id) continue;
    const toolName = payload.invocation?.tool;
    if (toolName !== "search_experience" && toolName !== "read_experience") continue;

    const item = {
      toolName,
      toolInput: payload.invocation?.arguments || {},
    };
    if (toolName === "search_experience") {
      const output = parseJsonObject(mcpResultText(payload.result));
      const results = Array.isArray(output?.results) ? output.results : [];
      item.compactOutput = JSON.stringify({
        results: results
          .map((result) => ({ uri: String(result?.uri || "").trim() }))
          .filter((result) => result.uri),
      });
    }
    metadata.set(payload.call_id, item);
  }
  return metadata;
}

export function extractCaptureTurns(rolloutEntries, cfg = {}) {
  const usageMetadata = collectExperienceUsageMetadata(rolloutEntries);
  const turns = extractSharedCaptureTurns(normalizeCodexMcpToolEvents(rolloutEntries), cfg);
  return turns.map((turn) => ({
    ...turn,
    parts: turn.parts.map((part) => {
      if (part?.tool_status !== "completed" || !part.tool_id) return part;
      const metadata = usageMetadata.get(part.tool_id);
      if (!metadata) return part;

      const updated = { ...part, tool_input: metadata.toolInput };
      if (metadata.toolName === "search_experience" && !parseJsonObject(part.tool_output)) {
        updated.tool_output = metadata.compactOutput;
      }
      return updated;
    }),
  }));
}
