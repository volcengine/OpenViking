import { MCP_SERVER_NAME } from "./config.mjs";
import { buildGuardMessage, findVikingUriInKeys } from "./shared/uri-guard.mjs";

/** Model-facing name of a bridged OpenViking MCP tool. */
const mcp = rawName => `mcp__${MCP_SERVER_NAME}__${rawName}`;

const FILE_PATH_KEYS = ["filePath", "file_path", "filepath", "path"];

const GUARDED_TOOLS = {
  read: {
    keys: FILE_PATH_KEYS,
    tool: mcp("read"),
    example: uri => `${mcp("read")}(uris="${uri}")`,
  },
  glob: {
    keys: [...FILE_PATH_KEYS, "pattern"],
    tool: mcp("list"),
    example: uri => `${mcp("list")}(uri="${uri}")`,
  },
  grep: {
    keys: FILE_PATH_KEYS,
    tool: mcp("grep"),
    example: (uri, args) =>
      `${mcp("grep")}(pattern="${escapeText(args?.pattern)}", uri="${uri}")`,
  },
  bash: {
    keys: ["command"],
    tool: `${mcp("read")} or ${mcp("search")}`,
    example: uri => `${mcp("read")}(uris="${uri}")`,
  },
  edit: {
    keys: FILE_PATH_KEYS,
    tool: mcp("edit"),
    example: uri => `${mcp("edit")}(uri="${uri}", old_string="...", new_string="...")`,
  },
  write: {
    keys: FILE_PATH_KEYS,
    tool: mcp("write"),
    example: uri => `${mcp("write")}(uri="${uri}", content="...")`,
  },
  str_replace_editor: {
    keys: FILE_PATH_KEYS,
    tool: "the OpenViking MCP tools",
    example: uri => `${mcp("read")}(uris="${uri}")`,
  },
};

export async function guardVikingUri(exec, next) {
  const hint = GUARDED_TOOLS[exec.name];
  if (!hint) return next();
  const uri = findVikingUriInKeys(exec.arguments, hint.keys);
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
