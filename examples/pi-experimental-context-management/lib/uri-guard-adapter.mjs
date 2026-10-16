import { buildGuardMessage, findVikingUri, normalizeToolName } from "../shared/uri-guard.mjs";

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
  const toolName = normalizeToolName(event?.toolName ?? event?.tool_name ?? event?.name);
  const hint = VIKING_URI_TOOL_HINTS[toolName];
  if (!hint) return null;

  const input = event?.input ?? event?.args ?? event?.params ?? {};
  const uri = findVikingUri(input);
  if (!uri) return null;

  return {
    block: true,
    reason: buildGuardMessage(uri, {
      tool: hint.tool,
      example: hint.example(uri, input),
    }),
  };
}
