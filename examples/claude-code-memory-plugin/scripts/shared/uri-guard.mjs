// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
import { readFileSync, realpathSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_URI_KEYS = [
  "filePath",
  "file_path",
  "filepath",
  "path",
  "uri",
  "target_uri",
  "targetUri",
];

export function normalizeToolName(value) {
  return String(value || "").trim().toLowerCase();
}

// Arguments that carry file CONTENT rather than a location. The sweep below
// looks past the known path keys so an unusual one (`paths`, a nested target)
// is still caught, but text a tool is asked to WRITE is not a path: a local
// `write` whose body merely mentions viking://user/default/ was denied, and no
// file was created. Skipped by name at any depth.
const DEFAULT_CONTENT_KEYS = [
  "content",
  "contents",
  "text",
  "body",
  "old_string",
  "oldString",
  "new_string",
  "newString",
  "old_str",
  "new_str",
  "file_text",
  "insert_line",
  "replacement",
];

export function findVikingUri(args = {}, keys = DEFAULT_URI_KEYS, contentKeys = DEFAULT_CONTENT_KEYS) {
  if (!args || typeof args !== "object") return null;
  for (const key of keys) {
    const uri = findVikingUriInValue(args[key]);
    if (uri) return uri;
  }
  return findVikingUriInValue(args, new Set(contentKeys));
}

export function findVikingUriInValue(value, skipKeys) {
  if (typeof value === "string") {
    const match = value.match(/\bviking:\/\/[^\s"'`<>)]*/i);
    return match?.[0] || null;
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      const uri = findVikingUriInValue(item, skipKeys);
      if (uri) return uri;
    }
    return null;
  }
  if (value && typeof value === "object") {
    for (const [key, item] of Object.entries(value)) {
      if (skipKeys?.has(key)) continue;
      const uri = findVikingUriInValue(item, skipKeys);
      if (uri) return uri;
    }
  }
  return null;
}

export function buildGuardMessage(uri, hint = {}) {
  const tool = hint.tool || "the OpenViking MCP tools";
  const example = typeof hint.example === "function" ? hint.example(uri) : hint.example;
  const lines = [
    "viking:// URIs are OpenViking virtual paths, not local filesystem paths.",
    `Use ${tool} instead.`,
  ];
  if (example) lines.push(`Example: ${example}`);
  return lines.join("\n");
}

export function buildGuardNotice(uri, hint = {}) {
  const lines = [
    `[OpenViking memory plugin] URI guard: this shell command contains the viking:// URI ${uri}.`,
    "viking:// URIs are OpenViking virtual paths, not local files, so cat, ls, grep and other file commands cannot open them.",
    `If you meant to read or search OpenViking content, use ${hint.tool || "the OpenViking MCP tools"} instead.`,
  ];
  if (hint.example) lines.push(`Example: ${hint.example}`);
  lines.push("If the URI is intentional data (an ov CLI argument, an HTTP payload, a search pattern), ignore this notice.");
  return lines.join("\n");
}

const SKILL_URI_RE = /^viking:\/\/(?:~|user\/[^/]+|agent)\/skills(?:\/|$)/i;
const SHARED_SKILL_URI_RE = /^viking:\/\/agent\/skills(?:\/|$)/i;
// The skills root, a skill directory, or its SKILL.md; anything deeper is a helper file.
const SKILL_MD_URI_RE = /^viking:\/\/(?:~|user\/[^/]+|agent)\/skills(?:\/[^/]+(?:\/SKILL\.md)?)?\/?$/i;

/**
 * Skills are installed through the MCP add_skill tool: write and edit refuse
 * the user's own skills subtree, and under the shared root they would bypass
 * installation.
 */
export function isSkillUri(uri) {
  return SKILL_URI_RE.test(String(uri || ""));
}

/**
 * The add_skill call that replaces a write or edit aimed at `uri`. A shared
 * skill goes back to the shared root: without target_uri, add_skill makes a
 * private copy that then shadows it.
 */
export function addSkillExample(uri, { call = "add_skill", edited = false } = {}) {
  const value = String(uri || "");
  const shared = SHARED_SKILL_URI_RE.test(value) ? ', target_uri="viking://agent/skills"' : "";
  return SKILL_MD_URI_RE.test(value)
    ? `${call}(data="<the full ${edited ? "edited " : ""}SKILL.md text>"${shared})`
    : `${call}(path="<local skill folder or .zip with the changed files>"${shared})`;
}

/** The hints a host gets when it names no table of its own. */
export const DEFAULT_TOOL_HINTS = {
  read: {
    tool: "OpenViking MCP read",
    example: (uri) => `read(uri="${uri}")`,
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
  edit: {
    tool: (uri) => (isSkillUri(uri) ? "OpenViking MCP add_skill" : "OpenViking MCP edit"),
    example: (uri) => (
      isSkillUri(uri)
        ? addSkillExample(uri, { edited: true })
        : `edit(uri="${uri}", old_string="...", new_string="...")`
    ),
  },
  write: {
    tool: (uri) => (isSkillUri(uri) ? "OpenViking MCP add_skill" : "OpenViking MCP write"),
    example: (uri) => (
      isSkillUri(uri) ? addSkillExample(uri) : `write(uri="${uri}", content="...")`
    ),
  },
  bash: {
    tool: "OpenViking MCP read or search",
    example: (uri) => `read(uri="${uri}")`,
  },
  runcommand: {
    tool: "OpenViking MCP read or search",
    example: (uri) => `read(uri="${uri}")`,
  },
  shell: {
    tool: "OpenViking MCP read or search",
    example: (uri) => `read(uri="${uri}")`,
  },
};

// Tools whose argument is a command line rather than a location. When a shell
// command carries a viking:// URI as legitimate data (an `ov` argument, an HTTP
// payload, a search pattern, or a heredoc), no notice is necessary. When it
// attempts to access the URI as a local filesystem path (cat, ls, etc.), the
// command still runs but the model receives a notice.
// Only a name the host's hint table lists is guarded at all, so the PowerShell
// names reach no host that does not map them (dsh's Windows `pwsh` tool does).
const SHELL_TOOL_NAMES = new Set(["bash", "shell", "runcommand", "pwsh", "powershell"]);

const DATA_CARRIER_COMMANDS = new Set([
  "ov",
  "ovcli",
  "curl",
  "wget",
  "http",
  "httpx",
]);

const SEARCH_COMMANDS = new Set([
  "grep",
  "egrep",
  "fgrep",
  "rg",
  "ag",
  "ack",
]);

function normalizeBinary(token) {
  if (!token) return "";
  const base = token.split(/[\/\\]/).pop() || "";
  return base.replace(/\.exe$/i, "").toLowerCase();
}

function isInsideHeredoc(command, uri) {
  const heredocRegex = /<<-?\s*['"\\]?(\w+)['"]?([\s\S]*?)(?:\n[ \t]*\1\b|$)/g;
  let match;
  while ((match = heredocRegex.exec(command)) !== null) {
    if (match[2] && match[2].includes(uri)) {
      return true;
    }
  }
  return false;
}

function isSearchPattern(tokens, uri) {
  let patternFound = false;
  let optionsEnded = false;
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    if (!optionsEnded) {
      if (token === "--") {
        optionsEnded = true;
        continue;
      }
      if (token === "-e" || token === "--regexp") {
        patternFound = true;
        if (i + 1 < tokens.length) {
          if (tokens[i + 1].includes(uri)) return true;
          i++;
        }
        continue;
      }
      if (token.startsWith("-e=") || token.startsWith("--regexp=")) {
        patternFound = true;
        const val = token.slice(token.indexOf("=") + 1);
        if (val) {
          if (val.includes(uri)) return true;
        } else if (i + 1 < tokens.length) {
          if (tokens[i + 1].includes(uri)) return true;
          i++;
        }
        continue;
      }
      if (
        token === "-f" || token === "--file" ||
        token === "-m" || token === "--max-count" ||
        token === "-C" || token === "--context" ||
        token === "-B" || token === "--before-context" ||
        token === "-A" || token === "--after-context" ||
        token === "-g" || token === "--glob" ||
        token === "-t" || token === "--type" ||
        token === "-T" || token === "--type-not" ||
        token === "--exclude" || token === "--include" ||
        token === "--exclude-dir"
      ) {
        i++;
        continue;
      }
      if (token.startsWith("-")) {
        continue;
      }
    }
    if (!patternFound) {
      patternFound = true;
      if (token.includes(uri)) return true;
    }
  }
  return false;
}

function tokenizeShellSegment(seg) {
  const tokens = [];
  const regex = /[^\s"']+|"([^"\\]*(?:\\.[^"\\]*)*)"|'([^'\\]*(?:\\.[^'\\]*)*)'/g;
  let match;
  while ((match = regex.exec(seg)) !== null) {
    tokens.push(match[1] !== undefined ? match[1] : (match[2] !== undefined ? match[2] : match[0]));
  }
  return tokens;
}

function isTokensDataCarrier(tokens, uri) {
  if (!tokens || tokens.length === 0) return false;
  let cmdIdx = 0;
  while (cmdIdx < tokens.length) {
    const tok = tokens[cmdIdx];
    if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(tok) || tok === "sudo" || tok === "env" || tok === "nohup") {
      cmdIdx++;
    } else {
      break;
    }
  }
  if (cmdIdx >= tokens.length) return false;

  const binary = normalizeBinary(tokens[cmdIdx]);
  if (DATA_CARRIER_COMMANDS.has(binary)) return true;
  if (SEARCH_COMMANDS.has(binary)) {
    return isSearchPattern(tokens.slice(cmdIdx + 1), uri);
  }
  return false;
}

export function isShellDataCarrier(command, uri) {
  if (!command || !uri) return false;

  if (Array.isArray(command)) {
    return isTokensDataCarrier(command, uri);
  }

  const str = String(command);
  if (isInsideHeredoc(str, uri)) return true;

  const segments = str.split(/(?:&&|\|\||[;\n|&])/);
  let uriSegmentCount = 0;
  for (const seg of segments) {
    if (!seg.includes(uri)) continue;
    uriSegmentCount++;
    const tokens = tokenizeShellSegment(seg);
    if (!isTokensDataCarrier(tokens, uri)) {
      return false;
    }
  }

  return uriSegmentCount > 0;
}

function findCarrierCommand(input, uri) {
  if (typeof input === "string") {
    return input.includes(uri) ? input : null;
  }
  if (Array.isArray(input)) {
    return input.some((item) => typeof item === "string" && item.includes(uri)) ? input : null;
  }
  if (!input || typeof input !== "object") return null;

  if (input.command !== undefined) {
    const found = findCarrierCommand(input.command, uri);
    if (found) return found;
  }
  if (input.cmd !== undefined) {
    const found = findCarrierCommand(input.cmd, uri);
    if (found) return found;
  }
  for (const [k, v] of Object.entries(input)) {
    if (k === "command" || k === "cmd") continue;
    const found = findCarrierCommand(v, uri);
    if (found) return found;
  }
  return null;
}

// `pattern` is where glob looks but what grep looks for: a grep for the text
// "viking://" in a local tree is not a path. The generic sweep still reaches
// glob's pattern.
const TEXT_ARGS_BY_TOOL = { grep: ["pattern"] };

function resolveGuardedUri(toolName, input, { hints = DEFAULT_TOOL_HINTS } = {}) {
  const name = normalizeToolName(toolName);
  const hint = hints[name];
  if (!hint) return null;
  const textArgs = TEXT_ARGS_BY_TOOL[name];
  const uri = findVikingUri(input, DEFAULT_URI_KEYS, textArgs ? [...DEFAULT_CONTENT_KEYS, ...textArgs] : DEFAULT_CONTENT_KEYS);
  if (!uri) return null;
  return {
    uri,
    shell: SHELL_TOOL_NAMES.has(name),
    hint: {
      tool: typeof hint.tool === "function" ? hint.tool(uri, input) : hint.tool,
      example: typeof hint.example === "function" ? hint.example(uri, input) : hint.example,
    },
  };
}

/**
 * The deny decision for one tool call, or null when the call may proceed.
 *
 * A file tool whose path is a viking:// URI cannot succeed, and a write would
 * leave a junk local file, so it is denied. A shell tool is never denied here;
 * `evaluateUriNotice` covers it. `hints` carries the host's replacement tool
 * names and example calls, and a tool without a hint is not guarded.
 */
export function evaluateUriGuard(toolName, input = {}, opts = {}) {
  const match = resolveGuardedUri(toolName, input, opts);
  if (!match || match.shell) return null;
  return { uri: match.uri, reason: buildGuardMessage(match.uri, match.hint) };
}

/** The notice for a shell command that carries a viking:// URI, or null. The command still runs. */
export function evaluateUriNotice(toolName, input = {}, opts = {}) {
  const match = resolveGuardedUri(toolName, input, opts);
  if (!match?.shell) return null;
  const cmd = findCarrierCommand(input, match.uri);
  if (cmd && isShellDataCarrier(cmd, match.uri)) return null;
  return { uri: match.uri, reason: buildGuardNotice(match.uri, match.hint) };
}

/** The PreToolUse deny envelope claude-code, trae and zcode all read. */
export function denyHookSpecificOutput(reason) {
  return {
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: reason,
    },
  };
}

/** The PreToolUse notice envelope: no permissionDecision, so the host's own permission flow still runs. */
export function noticeHookSpecificOutput(reason) {
  return { hookSpecificOutput: { hookEventName: "PreToolUse", additionalContext: reason } };
}

/** A hook event's tool name and input, under any of the spellings the hosts use. */
export function readToolEvent(event = {}) {
  return {
    toolName: event.tool_name ?? event.toolName ?? event.name ?? event.tool,
    toolInput: event.tool_input ?? event.toolInput ?? event.input ?? {},
  };
}

/** The whole PreToolUse guard: a deny envelope, a notice envelope, or {}. */
export function preToolUseOutput(event = {}, opts = {}) {
  const { toolName, toolInput } = readToolEvent(event);
  const denied = evaluateUriGuard(toolName, toolInput, opts);
  if (denied) return denyHookSpecificOutput(denied.reason);
  const notice = evaluateUriNotice(toolName, toolInput, opts);
  return notice ? noticeHookSpecificOutput(notice.reason) : {};
}

/** Cursor's beforeReadFile deny envelope. */
export function denyCursorPermission(reason) {
  return { permission: "deny", user_message: reason };
}

function readHookInput() {
  try {
    const raw = readFileSync(0, "utf8").trim();
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

/**
 * Run a guard as a hook process: read the event off stdin, print the envelope.
 *
 * A guard is also imported by its harness tests, so the body only runs when the
 * module is the process entrypoint. An empty envelope prints nothing — every
 * host treats unrecognized or empty output as "no opinion", and one of them
 * rejects any key it does not know.
 */
function isEntrypoint(moduleUrl) {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(moduleUrl));
  } catch {
    return resolve(process.argv[1]) === fileURLToPath(moduleUrl);
  }
}

export function runUriGuardHook(moduleUrl, evaluate) {
  if (!isEntrypoint(moduleUrl)) return;
  const output = evaluate(readHookInput());
  if (Object.keys(output).length > 0) process.stdout.write(`${JSON.stringify(output)}\n`);
}
