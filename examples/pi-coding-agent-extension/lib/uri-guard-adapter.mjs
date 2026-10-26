import { evaluateUriGuard, evaluateUriNotice, normalizeToolName } from "../shared/uri-guard.mjs";

// pi's builtin file tools mapped to the OpenViking MCP tools the bridge
// registers. Every example is a valid call against the server's own schemas:
// `read` takes a `uris` array, `grep` takes a `uri` plus a `pattern` array,
// `glob` takes a pattern plus an optional `uri`, and `write`/`edit` address
// content by `uri`. Tools absent from this table are never guarded, so the
// openviking_* tools themselves need no allowlist.
const VIKING_URI_TOOL_HINTS = {
  read: {
    tool: "openviking_read",
    example: (uri) => `openviking_read(uris=["${uri}"])`,
  },
  grep: {
    tool: "openviking_grep",
    example: (uri, input = {}) => `openviking_grep(uri="${uri}", pattern=["${String(input.pattern ?? "").replaceAll('"', '\\"')}"])`,
  },
  find: {
    tool: "openviking_glob",
    example: (uri) => `openviking_glob(uri="${uri}", pattern="**/*")`,
  },
  ls: {
    tool: "openviking_list",
    example: (uri) => `openviking_list(uri="${uri}")`,
  },
  write: {
    tool: "openviking_write",
    example: (uri) => `openviking_write(uri="${uri}", content="...")`,
  },
  edit: {
    tool: "openviking_edit",
    example: (uri) => `openviking_edit(uri="${uri}", old_string="...", new_string="...")`,
  },
  bash: {
    tool: "openviking_read or openviking_search",
    example: (uri) => `openviking_read(uris=["${uri}"])`,
  },
};

// The only keys the guard may see for a given tool. pi's edit takes
// `{ path, edits: [{ oldText, newText }] }` (plus a legacy top-level
// oldText/newText pair), and those replacement-text keys are not in the shared
// guard's content-key allowlist. Handed the whole input, its generic sweep
// reads them as locations and blocks any edit whose new text merely mentions a
// viking:// URI — which fires the moment somebody edits this repo's own docs.
// Only `path` says where the edit lands, so only `path` gets through.
const GUARD_INPUT_KEYS_BY_TOOL = { edit: ["path"] };

function narrowGuardInput(toolName, input) {
  const keys = GUARD_INPUT_KEYS_BY_TOOL[normalizeToolName(toolName)];
  if (!keys) return input;
  if (!input || typeof input !== "object") return {};
  return Object.fromEntries(keys.filter((key) => input[key] !== undefined).map((key) => [key, input[key]]));
}

function readPiToolEvent(event) {
  const toolName = event?.toolName ?? event?.tool_name ?? event?.name;
  return {
    toolName,
    input: narrowGuardInput(toolName, event?.input ?? event?.args ?? event?.params ?? {}),
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
