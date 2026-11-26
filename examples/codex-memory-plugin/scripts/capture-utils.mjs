import {
  extractCaptureTurns as extractSharedCaptureTurns,
} from "./shared/capture-utils.mjs";

// Named one by one rather than re-exported wholesale: this module has an
// `extractCaptureTurns` of its own, and `export *` would let the shared one
// through under the same name with nothing to say which a caller holds.
export { findLastHumanTurnIndex } from "./shared/capture-utils.mjs";

function mcpResultText(result) {
  let value = result;
  if (Object.prototype.hasOwnProperty.call(result || {}, "Ok")) value = result.Ok;
  else if (Object.prototype.hasOwnProperty.call(result || {}, "Err")) value = result.Err;
  if (Array.isArray(value?.content)) {
    const text = value.content
      .filter((part) => part?.type === "text" && typeof part.text === "string")
      .map((part) => part.text)
      .join("\n");
    if (text) return text;
  }
  if (typeof value === "string") return value;
  return value == null ? "" : JSON.stringify(value);
}

const RESPONSE_TOOL_TYPES = new Set([
  "function_call",
  "function_call_output",
  "custom_tool_call",
  "custom_tool_call_output",
]);
const COMPLETED_TURN_TOOL_TYPES = new Set([
  "CommandExecution",
  "DynamicToolCall",
  "FileChange",
  "McpToolCall",
]);

const FAILED_TOOL_STATUSES = new Set(["failed", "error", "declined"]);
const TERMINAL_TOOL_STATUSES = new Set(["completed", ...FAILED_TOOL_STATUSES]);

function inheritedEntry(entry, type, payload) {
  const turnId = entry?.payload?.turn_id;
  return {
    ...entry,
    type,
    payload: turnId != null && payload?.turn_id == null
      ? { ...payload, turn_id: turnId }
      : payload,
  };
}

function expandCodexRolloutEntries(rolloutEntries) {
  return (rolloutEntries || []).flatMap((entry) => {
    const payload = entry?.payload;
    if (entry?.type === "history_mutation" && payload?.operation === "append") {
      if (!Array.isArray(payload.items)) return [entry];
      return payload.items.map((item) => inheritedEntry(entry, "response_item", item));
    }
    if (
      entry?.type === "event_msg"
      && payload?.type === "item_completed"
      && COMPLETED_TURN_TOOL_TYPES.has(payload.item?.type)
    ) {
      return [inheritedEntry(entry, "turn_item", payload.item)];
    }
    return [entry];
  });
}

function compactObject(value) {
  return Object.fromEntries(
    Object.entries(value).filter(([, item]) => item !== undefined && item !== null),
  );
}

function joinedOutput(payload) {
  return [payload?.stdout, payload?.stderr].filter(Boolean).join("\n");
}

function isFailed(payload) {
  return payload?.success === false
    || (typeof payload?.exit_code === "number" && payload.exit_code !== 0)
    || FAILED_TOOL_STATUSES.has(String(payload?.status || "").toLowerCase())
    || Boolean(payload?.error);
}

function completedToolEvent(entry) {
  const payload = entry?.payload;
  if (payload?.type === "mcp_tool_call_end") {
    const hasResult = Object.prototype.hasOwnProperty.call(payload.result || {}, "Ok");
    const failed = !hasResult || payload.result.Ok?.isError === true;
    return {
      kind: "mcp",
      id: payload.call_id,
      name: payload.invocation?.tool,
      input: payload.invocation?.arguments ?? {},
      output: mcpResultText(payload.result),
      failed,
    };
  }
  if (payload?.type === "McpToolCall") {
    return {
      kind: "mcp",
      id: payload.id,
      name: payload.tool,
      input: payload.arguments ?? {},
      output: mcpResultText(payload.error ?? payload.result),
      failed: isFailed(payload) || payload.result?.isError === true,
    };
  }
  if (payload?.type === "exec_command_end" || payload?.type === "CommandExecution") {
    return {
      kind: "command",
      id: payload.call_id || payload.id,
      name: "exec_command",
      input: compactObject({
        command: payload.command,
        cwd: payload.cwd,
        source: payload.source,
        interaction_input: payload.interaction_input,
      }),
      output: payload.aggregated_output ?? payload.formatted_output ?? joinedOutput(payload),
      failed: isFailed(payload),
    };
  }
  if (payload?.type === "patch_apply_end" || payload?.type === "FileChange") {
    return {
      kind: "patch",
      id: payload.call_id || payload.id,
      name: "apply_patch",
      input: compactObject({
        changes: payload.changes,
        auto_approved: payload.auto_approved,
      }),
      output: joinedOutput(payload),
      failed: isFailed(payload),
    };
  }
  if (payload?.type === "DynamicToolCall") {
    return {
      kind: "dynamic",
      id: payload.id,
      name: payload.tool,
      input: payload.arguments ?? {},
      output: payload.error ?? payload.content_items ?? [],
      failed: isFailed(payload),
    };
  }
  return null;
}

function completedToolKey(tool) {
  return [tool.kind, tool.id, tool.name].join("\0");
}

function completedToolPriority(entry) {
  const source = entry?.type === "turn_item" ? 2 : 0;
  const status = String(entry?.payload?.status || "").toLowerCase();
  return source + (TERMINAL_TOOL_STATUSES.has(status) ? 1 : 0);
}

function responseItemKey(entry) {
  if (entry?.type !== "response_item") return "";
  const payload = entry.payload;
  if (!payload?.type) return "";
  const id = payload.call_id || payload.id;
  if (!id) return "";
  const kind = payload.type === "custom_tool_call"
    ? "function_call"
    : payload.type === "custom_tool_call_output"
      ? "function_call_output"
      : payload.type;
  return `${kind}\0${id}`;
}

function deduplicateCodexToolEvents(rolloutEntries) {
  const entries = [];
  const responseItems = new Set();
  for (const entry of rolloutEntries || []) {
    const key = responseItemKey(entry);
    if (key && responseItems.has(key)) continue;
    if (key) responseItems.add(key);
    entries.push(entry);
  }

  const completedTools = new Map();
  const preferredCompleted = new Map();
  for (const entry of entries) {
    const tool = completedToolEvent(entry);
    if (!tool?.id || !tool.name) continue;
    completedTools.set(entry, tool);
    const key = completedToolKey(tool);
    const previous = preferredCompleted.get(key);
    if (!previous || completedToolPriority(entry) >= completedToolPriority(previous)) {
      preferredCompleted.set(key, entry);
    }
  }
  const completedNamesById = new Map();
  for (const entry of preferredCompleted.values()) {
    const tool = completedTools.get(entry);
    const names = completedNamesById.get(tool.id) ?? new Set();
    names.add(tool.name);
    completedNamesById.set(tool.id, names);
  }
  const responseNamesById = new Map();
  for (const entry of entries) {
    const payload = entry?.payload;
    if (
      entry?.type === "response_item"
      && RESPONSE_TOOL_TYPES.has(payload?.type)
      && payload?.name
    ) {
      responseNamesById.set(payload.call_id || payload.id, payload.name);
    }
  }

  return entries.flatMap((entry) => {
    const tool = completedTools.get(entry);
    if (tool?.id && tool.name) {
      return preferredCompleted.get(completedToolKey(tool)) === entry ? [entry] : [];
    }
    const payload = entry?.payload;
    const id = payload?.call_id || payload?.id;
    const name = payload?.name || responseNamesById.get(id);
    const completedNames = completedNamesById.get(id);
    if (!RESPONSE_TOOL_TYPES.has(payload?.type) || !completedNames) return [entry];
    if (completedNames.has(name)) return [];
    if (name === "exec") {
      return [{
        ...entry,
        payload: { ...payload, call_id: `codex-container:${id}` },
      }];
    }
    return [];
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

function normalizeCodexCompletedToolEvents(rolloutEntries) {
  return (rolloutEntries || []).flatMap((entry) => {
    const tool = completedToolEvent(entry);
    if (!tool?.id || !tool.name) return [entry];

    const call = inheritedEntry(entry, "response_item", {
      type: "function_call",
      id: tool.id,
      call_id: tool.id,
      name: tool.name,
      arguments: tool.input,
    });
    const result = inheritedEntry(entry, "response_item", {
      type: "function_call_output",
      call_id: tool.id,
      status: tool.failed ? "error" : "completed",
      ...(tool.failed
        ? { error: tool.output || `${tool.name} failed` }
        : { output: tool.output }),
    });
    return [call, result];
  });
}

// Codex writes these host-provided blocks as a user message before its first
// turn_context. Match the complete wrapper, not a tag mentioned by a user.
const STARTUP_BLOCKS = [
  /^<recommended_plugins>\r?\nHere is a list of plugins that are available but not installed\.[\s\S]*?\r?\n<\/recommended_plugins>$/,
  /^# AGENTS\.md instructions(?: for [^\r\n]+)?\r?\n\r?\n<INSTRUCTIONS>\r?\n[\s\S]*?\r?\n<\/INSTRUCTIONS>$/,
  /^<environment_context>\r?\n\s*<cwd>[^\r\n]*<\/cwd>\r?\n\s*<shell>[^\r\n]*<\/shell>\r?\n\s*<current_date>[^\r\n]*<\/current_date>\r?\n\s*<timezone>[^\r\n]*<\/timezone>[\s\S]*?<\/environment_context>$/,
];

function stripStartupBlocks(text) {
  const source = String(text);
  // Blocks can share an input_text part with a real prompt. A complete host
  // block starts on its own line and ends there or at end of text.
  const candidates = [
    /<recommended_plugins>\r?\nHere is a list of plugins that are available but not installed\.[\s\S]*?\r?\n<\/recommended_plugins>/g,
    /# AGENTS\.md instructions(?: for [^\r\n]+)?\r?\n\r?\n<INSTRUCTIONS>\r?\n[\s\S]*?\r?\n<\/INSTRUCTIONS>/g,
    /<environment_context>\r?\n\s*<cwd>[^\r\n]*<\/cwd>\r?\n\s*<shell>[^\r\n]*<\/shell>\r?\n\s*<current_date>[^\r\n]*<\/current_date>\r?\n\s*<timezone>[^\r\n]*<\/timezone>[\s\S]*?<\/environment_context>/g,
  ];
  const matches = [];
  for (let i = 0; i < candidates.length; i += 1) {
    for (const match of source.matchAll(candidates[i])) {
      const start = match.index;
      const end = start + match[0].length;
      if (start > 0 && !/\r?\n$/.test(source.slice(0, start))) continue;
      if (end < source.length && !/^\r?\n/.test(source.slice(end))) continue;
      if (STARTUP_BLOCKS[i].test(match[0])) matches.push({ start, end });
    }
  }
  if (!matches.length) return source;
  matches.sort((a, b) => a.start - b.start);
  let output = "";
  let cursor = 0;
  for (const { start, end } of matches) {
    if (start < cursor) continue;
    output += source.slice(cursor, start);
    cursor = end;
  }
  return (output + source.slice(cursor)).trim();
}

function withoutStartupContext(entries, cfg) {
  const filtered = [];
  const excludedLegacyTurnIndices = [];
  let beforeFirstTurn = true;
  for (const [index, entry] of entries.entries()) {
    if (entry?.type === "turn_context") beforeFirstTurn = false;
    const payload = entry?.payload;
    if (
      !beforeFirstTurn || entry?.type !== "response_item"
      || payload?.type !== "message" || payload.role !== "user"
      || !Array.isArray(payload.content)
    ) {
      filtered.push(entry);
      continue;
    }
    let changed = false;
    const content = payload.content.flatMap((part) => {
      if (part?.type !== "input_text" || typeof part.text !== "string") return [part];
      const text = stripStartupBlocks(part.text);
      if (text === part.text) return [part];
      changed = true;
      return text ? [{ ...part, text }] : [];
    });
    if (!changed) {
      filtered.push(entry);
    } else if (content.length) {
      filtered.push({ ...entry, payload: { ...payload, content } });
    } else {
      // Use the old extractor to locate this message in the old cursor's
      // coordinate system. Capture filters may already have dropped it.
      const prefix = extractSharedCaptureTurns(entries.slice(0, index), cfg).length;
      if (extractSharedCaptureTurns(entries.slice(0, index + 1), cfg).length > prefix) {
        excludedLegacyTurnIndices.push(prefix);
      }
    }
  }
  return { entries: filtered, excludedLegacyTurnIndices };
}

export function extractCaptureTranscript(rolloutEntries, cfg = {}) {
  const expanded = expandCodexRolloutEntries(rolloutEntries);
  const deduplicated = deduplicateCodexToolEvents(expanded);
  const normalized = normalizeCodexNativeToolEvents(deduplicated);
  const completed = normalizeCodexCompletedToolEvents(normalized);
  const { entries, excludedLegacyTurnIndices } = withoutStartupContext(completed, cfg);
  return { turns: extractSharedCaptureTurns(entries, cfg), excludedLegacyTurnIndices };
}

export function extractCaptureTurns(rolloutEntries, cfg = {}) {
  return extractCaptureTranscript(rolloutEntries, cfg).turns;
}
