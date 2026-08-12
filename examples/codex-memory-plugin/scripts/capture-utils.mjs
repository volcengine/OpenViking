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

function deduplicateMcpFunctionEvents(rolloutEntries) {
  const mcpCallIds = new Set(
    (rolloutEntries || [])
      .filter((entry) => entry?.payload?.type === "mcp_tool_call_end")
      .map((entry) => entry.payload.call_id)
      .filter(Boolean),
  );
  if (mcpCallIds.size === 0) return rolloutEntries || [];

  const duplicatedTypes = new Set([
    "function_call",
    "function_call_output",
    "custom_tool_call",
    "custom_tool_call_output",
  ]);
  return (rolloutEntries || []).filter((entry) => {
    const payload = entry?.payload;
    return !(
      duplicatedTypes.has(payload?.type)
      && mcpCallIds.has(payload.call_id || payload.id)
    );
  });
}

function agentMessageText(payload) {
  const content = Array.isArray(payload?.content) ? payload.content : [];
  return content
    .filter((part) => (
      (part?.type === "input_text" || part?.type === "output_text" || part?.type === "text")
      && typeof part.text === "string"
    ))
    .map((part) => part.text)
    .join("\n")
    .trim();
}

function normalizeCodexNativeToolEvents(rolloutEntries) {
  return (rolloutEntries || []).map((entry) => {
    const payload = entry?.payload;
    if (entry?.type === "response_item" && payload?.type === "agent_message") {
      const text = agentMessageText(payload);
      const author = String(payload.author || "subagent");
      const recipient = String(payload.recipient || "main");
      return {
        ...entry,
        payload: {
          type: "message",
          role: "assistant",
          content: [{
            type: "output_text",
            text: `[agent-message ${author} -> ${recipient}]${text ? `\n${text}` : ""}`,
          }],
        },
      };
    }
    if (payload?.type === "sub_agent_activity") {
      return {
        ...entry,
        payload: {
          type: "tool_result",
          call_id: `subagent-activity:${payload.event_id}`,
          name: "sub_agent_activity",
          output: {
            agent_thread_id: payload.agent_thread_id,
            agent_path: payload.agent_path,
            kind: payload.kind,
            occurred_at_ms: payload.occurred_at_ms,
          },
          status: "completed",
        },
      };
    }
    if (payload?.type === "custom_tool_call") {
      return {
        ...entry,
        payload: {
          type: "function_call",
          call_id: payload.call_id || payload.id,
          name: payload.name,
          arguments: payload.input,
        },
      };
    }
    if (payload?.type === "custom_tool_call_output") {
      return {
        ...entry,
        payload: {
          type: "function_call_output",
          call_id: payload.call_id || payload.id,
          output: payload.output,
        },
      };
    }
    if (payload?.type === "tool_search_call") {
      return {
        ...entry,
        payload: {
          type: "function_call",
          call_id: payload.call_id || payload.id,
          name: "tool_search",
          arguments: payload.arguments || {},
        },
      };
    }
    if (payload?.type === "tool_search_output") {
      return {
        ...entry,
        payload: {
          type: "function_call_output",
          call_id: payload.call_id || payload.id,
          output: JSON.stringify({ tools: payload.tools || [] }),
        },
      };
    }
    return entry;
  });
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

export function extractCaptureTurns(rolloutEntries, cfg = {}) {
  const deduplicated = deduplicateMcpFunctionEvents(rolloutEntries);
  const normalized = normalizeCodexNativeToolEvents(deduplicated);
  return extractSharedCaptureTurns(normalizeCodexMcpToolEvents(normalized), cfg);
}
