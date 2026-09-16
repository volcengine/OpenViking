import { evaluateUriGuard } from "../shared/uri-guard.mjs";

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

export function guardVikingUriToolCall(event) {
  const toolName = event?.toolName ?? event?.tool_name ?? event?.name;
  const input = event?.input ?? event?.args ?? event?.params ?? {};
  const decision = evaluateUriGuard(toolName, input, { hints: VIKING_URI_TOOL_HINTS });
  return decision ? { block: true, reason: decision.reason } : null;
}
