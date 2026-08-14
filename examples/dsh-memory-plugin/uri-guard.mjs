import { buildGuardMessage, findVikingUri } from "./shared/uri-guard.mjs";

const GUARDED_TOOLS = {
  read: {
    tool: "viking_read",
    example: uri => `viking_read(uri="${uri}", level="overview")`,
  },
  glob: {
    tool: "viking_browse",
    example: uri => `viking_browse(action="list", uri="${uri}")`,
  },
  grep: {
    tool: "viking_search",
    example: (uri, args) =>
      `viking_search(query="${escapeText(args?.pattern)}", scope="${uri}")`,
  },
  bash: {
    tool: "viking_read or viking_search",
    example: uri => `viking_read(uri="${uri}", level="overview")`,
  },
  edit: {
    tool: "OpenViking tools",
    example: uri => `viking_read(uri="${uri}", level="full")`,
  },
  write: {
    tool: "OpenViking tools",
    example: uri => `viking_read(uri="${uri}", level="full")`,
  },
  str_replace_editor: {
    tool: "OpenViking tools",
    example: uri => `viking_read(uri="${uri}", level="full")`,
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
