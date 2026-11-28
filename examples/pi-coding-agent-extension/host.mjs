/**
 * pi runs this extension in its own process; its callbacks build the payloads
 * below and run them through the shared flow. A payload carries `sessionId`
 * (pi's `getSessionId()`), `cwd`, and where it matters `branch` (pi's
 * `getBranch()`), `prompt`, `reason` and `forked`.
 */

import { deriveHarnessSessionId } from "./shared/session-model.mjs";
import { branchCursor, decode } from "./decode.mjs";
import { normalizeToolName } from "./shared/uri-guard.mjs";

// pi's builtin file tools mapped to the OpenViking MCP tools the bridge
// registers. Every example is a valid call against the server's own schemas:
// `read` takes a `uris` array, `grep` takes a `uri` plus a `pattern` array,
// `glob` takes a pattern plus an optional `uri`, and `write`/`edit` address
// content by `uri`. Tools absent from this table are never guarded, so the
// openviking_* tools themselves need no allowlist.
export const VIKING_URI_TOOL_HINTS = {
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

export function readPiToolEvent(event) {
  const toolName = event?.toolName ?? event?.tool_name ?? event?.name;
  return {
    toolName,
    input: narrowGuardInput(toolName, event?.input ?? event?.args ?? event?.params ?? {}),
  };
}

export const SESSION_PREFIX = "pi-";
export const HISTORY_TOOL = "openviking_session_history";

export const ovSessionIdFor = (piSessionId) => deriveHarnessSessionId(SESSION_PREFIX, String(piSessionId));

const hasCompaction = (branch) => branch.some((entry) => entry?.type === "compaction");
const hasMessages = (branch) => branch.some((entry) => entry?.type === "message");

/** @type {import("./shared/flow/host.d.mts").Host} */
const pi = {
  id: "pi",
  sessionPrefix: SESSION_PREFIX,
  events: {
    session_start: "start",
    // pi compacts inside a run and keeps going: the context restarts from the summary.
    session_compact: "start",
    // Recall runs in the provider-request hook, after pi has rendered the user message.
    context: "prompt",
    turn_end: "turnEnd",
    session_before_compact: "compactSoon",
    session_shutdown: "end",
    // `/viking commit`.
    viking_commit: "end",
    tool_call: "toolCall",
  },
  // pi hosts cap an extension handler at 30 s (omp logs `handler timed out
  // after 30000ms`), and the terminal waits for session_shutdown.
  budgets: {
    session_start: 25_000,
    session_compact: 25_000,
    context: 25_000,
    turn_end: 25_000,
    session_before_compact: 25_000,
    session_shutdown: 3_000,
    viking_commit: 25_000,
  },
  facts: {
    transcript: false,
    historyTool: `the OpenViking tool ${HISTORY_TOOL}`,
  },
  toolHints: VIKING_URI_TOOL_HINTS,
  read(event, payload) {
    const branch = Array.isArray(payload.branch) ? payload.branch : [];
    const sessionId = payload.sessionId ? String(payload.sessionId) : undefined;
    const read = {
      sessionId,
      session: sessionId ? ovSessionIdFor(sessionId) : undefined,
      cwd: payload.cwd || undefined,
      prompt: typeof payload.prompt === "string" ? payload.prompt.trim() : "",
      legacyCursor: sessionId ? branchCursor(branch, ovSessionIdFor(sessionId), Boolean(payload.forked)) : undefined,
    };
    if (event === "session_compact") return { ...read, resumed: "compact" };
    if (event === "session_start") {
      // /reload leaves the context as it was, so only a compaction in it needs the note.
      if (payload.reason === "reload") return { ...read, resumed: hasCompaction(branch) ? "compact" : "" };
      // A fork's earlier entries belong to its parent's session.
      return { ...read, resumed: hasMessages(branch) && !payload.forked ? "resume" : "" };
    }
    return read;
  },
  decode,
  legacyCursor: (_nativeSessionId, read) => read.legacyCursor,
  tool(payload) {
    const { toolName, input } = readPiToolEvent(payload);
    if (toolName === HISTORY_TOOL) return { history: input };
    return { name: toolName, input };
  },
  emit: (_event, output) => output,
};

export default pi;
