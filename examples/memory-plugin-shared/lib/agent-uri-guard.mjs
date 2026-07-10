import { buildGuardMessage, findVikingUri, normalizeToolName } from "./uri-guard.mjs";

const TOOL_HINTS = {
  read: {
    tool: "OpenViking MCP read",
    example: (uri) => `read(uris="${uri}")`,
  },
  glob: {
    tool: "OpenViking MCP glob or list",
    example: (uri, input = {}) => (
      `glob(pattern="${String(input.pattern ?? "**/*").replaceAll('"', '\\"')}", uri="${uri}")`
    ),
  },
  grep: {
    tool: "OpenViking MCP grep or search",
    example: (uri, input = {}) => (
      `grep(uri="${uri}", pattern="${String(input.pattern ?? "").replaceAll('"', '\\"')}")`
    ),
  },
  bash: {
    tool: "OpenViking MCP read or search",
    example: (uri) => `read(uris="${uri}")`,
  },
  runcommand: {
    tool: "OpenViking MCP read or search",
    example: (uri) => `read(uris="${uri}")`,
  },
  shell: {
    tool: "OpenViking MCP read or search",
    example: (uri) => `read(uris="${uri}")`,
  },
};

export function evaluateAgentUriGuard(toolName, input = {}) {
  const hint = TOOL_HINTS[normalizeToolName(toolName)];
  if (!hint) return null;
  const uri = findVikingUri(input);
  if (!uri) return null;
  return {
    uri,
    reason: buildGuardMessage(uri, {
      tool: hint.tool,
      example: hint.example(uri, input),
    }),
  };
}
