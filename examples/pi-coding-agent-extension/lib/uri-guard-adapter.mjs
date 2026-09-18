import { evaluateUriGuard, evaluateUriNotice } from "../shared/uri-guard.mjs";

const VIKING_URI_TOOL_HINTS = {
  read: {
    tool: "viking_read",
    example: (uri) => `viking_read(uri="${uri}", level="overview")`,
  },
  grep: {
    tool: "viking_search",
    example: (uri, input = {}) => `viking_search(query="${String(input.pattern ?? "").replaceAll('"', '\\"')}", scope="${uri}")`,
  },
  find: {
    tool: "viking_browse",
    example: (uri) => `viking_browse(action="list", uri="${uri}")`,
  },
  ls: {
    tool: "viking_browse",
    example: (uri) => `viking_browse(action="list", uri="${uri}")`,
  },
  bash: {
    tool: "viking_read or viking_search",
    example: (uri) => `viking_read(uri="${uri}", level="overview")`,
  },
};

function readPiToolEvent(event) {
  return {
    toolName: event?.toolName ?? event?.tool_name ?? event?.name,
    input: event?.input ?? event?.args ?? event?.params ?? {},
  };
}

export function guardVikingUriToolCall(event) {
  const { toolName, input } = readPiToolEvent(event);
  const decision = evaluateUriGuard(toolName, input, { hints: VIKING_URI_TOOL_HINTS });
  return decision ? { block: true, reason: decision.reason } : null;
}

// A tool_result handler's content replaces the result's content, so the
// original blocks are carried over and the notice is appended after them.
export function noticeVikingUriToolResult(event) {
  const { toolName, input } = readPiToolEvent(event);
  const notice = evaluateUriNotice(toolName, input, { hints: VIKING_URI_TOOL_HINTS });
  if (!notice) return null;
  const content = Array.isArray(event?.content) ? event.content : [];
  return { content: [...content, { type: "text", text: notice.reason }] };
}
