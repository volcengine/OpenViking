import { MCP_SERVER_NAME } from "./config.mjs";
import { buildGuardMessage, findVikingUri } from "./shared/uri-guard.mjs";

/** Model-facing name of a bridged OpenViking MCP tool. */
const mcp = rawName => `mcp__${MCP_SERVER_NAME}__${rawName}`;

const GUARDED_TOOLS = {
  read: {
    tool: mcp("read"),
    example: uri => `${mcp("read")}(uris="${uri}")`,
  },
  glob: {
    tool: mcp("list"),
    example: uri => `${mcp("list")}(uri="${uri}")`,
  },
  grep: {
    tool: mcp("grep"),
    example: (uri, args) =>
      `${mcp("grep")}(pattern="${escapeText(args?.pattern)}", uri="${uri}")`,
  },
  bash: {
    tool: `${mcp("read")} or ${mcp("search")}`,
    example: uri => `${mcp("read")}(uris="${uri}")`,
  },
  edit: {
    tool: mcp("edit"),
    example: uri => `${mcp("edit")}(uri="${uri}", old_string="...", new_string="...")`,
  },
  write: {
    tool: mcp("write"),
    example: uri => `${mcp("write")}(uri="${uri}", content="...")`,
  },
  str_replace_editor: {
    tool: "the OpenViking MCP tools",
    example: uri => `${mcp("read")}(uris="${uri}")`,
  },
};

export async function guardVikingUri(exec, next) {
  const hint = GUARDED_TOOLS[exec.name];
  if (!hint) return next();
  const uri = findVikingUri(exec.arguments);
  if (!uri) return next();
  return {
    kind: "deny",
    reason: buildGuardMessage(uri, {
      tool: hint.tool,
      example: hint.example(uri, exec.arguments),
    }),
  };
}

function escapeText(value) {
  return String(value || "").replaceAll('"', '\\"');
}
