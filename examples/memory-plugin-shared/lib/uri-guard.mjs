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
  "pattern",
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

// A shell command is program text, not a list of paths: it names a viking://
// URI as an operand (cat viking://file.md) far less often than it carries one
// as data — an HTTP payload, the ov CLI's own argument, a grep pattern, a
// heredoc that builds a JSON body. Scanning the whole command denied every one
// of those, so a shell hint reads operand positions instead.
const SHELL_WRAPPERS = new Set(["command", "doas", "env", "nohup", "sudo", "time"]);
const SHELL_FILE_COMMANDS = new Set([
  ".", "base64", "bat", "cat", "chmod", "chown", "code", "cp", "du", "emacs",
  "file", "head", "less", "ln", "ls", "md5", "md5sum", "mkdir", "more", "mv",
  "nano", "open", "readlink", "realpath", "rm", "rmdir", "sha1sum", "sha256sum",
  "shasum", "source", "stat", "strings", "tail", "tee", "touch", "tree", "vi",
  "vim", "wc", "xxd",
]);
// Their first non-option argument is a pattern or a program, never a path.
const SHELL_PATTERN_FIRST_COMMANDS = new Set(["awk", "egrep", "fgrep", "gawk", "grep", "rg", "sed"]);

/** The viking URI a shell command would open as a file, or null when it only carries one as data. */
export function findVikingUriInShellCommand(command) {
  const text = String(command || "");
  for (const segment of text.split(/[\n;|&()]+/)) {
    const words = segment.trim().split(/\s+/).filter(Boolean);
    let index = 0;
    while (index < words.length && (
      SHELL_WRAPPERS.has(words[index]) || /^[A-Za-z_][A-Za-z0-9_]*=/.test(words[index])
    )) index += 1;
    const name = (words[index] || "").replace(/^.*\//, "");
    const patternFirst = SHELL_PATTERN_FIRST_COMMANDS.has(name);
    if (!patternFirst && !SHELL_FILE_COMMANDS.has(name)) continue;
    let operand = false;
    for (let position = index + 1; position < words.length; position += 1) {
      const word = words[position].replace(/^["']|["']$/g, "");
      if (word.startsWith("-")) continue;
      if (patternFirst && !operand) {
        operand = true;
        continue;
      }
      const uri = findVikingUriInValue(word);
      if (uri) return uri;
      operand = true;
    }
  }
  const redirect = text.match(/(?:>>?|<)\s*["']?(viking:\/\/[^\s"'\x60<>)|;&]*)/i);
  return redirect ? redirect[1] : null;
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

/** The hints a host gets when it names no table of its own. */
export const DEFAULT_TOOL_HINTS = {
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
    shell: true,
    tool: "OpenViking MCP read or search",
    example: (uri) => `read(uris="${uri}")`,
  },
  runcommand: {
    shell: true,
    tool: "OpenViking MCP read or search",
    example: (uri) => `read(uris="${uri}")`,
  },
  shell: {
    shell: true,
    tool: "OpenViking MCP read or search",
    example: (uri) => `read(uris="${uri}")`,
  },
};

/**
 * The guard decision for one tool call, or null when the call may proceed.
 *
 * `hints` carries the host's replacement tool names and example calls; `guarded`
 * narrows which tool names the host guards at all, because the set is not the
 * same everywhere — claude-code and opencode leave the shell alone (their
 * matchers never see it), pi guards it. A hint marked `shell: true` describes
 * a shell command, whose URI is read from operand positions rather than the
 * whole command, because a shell command carries URIs as data far more often
 * than as paths.
 */
export function evaluateUriGuard(toolName, input = {}, { hints = DEFAULT_TOOL_HINTS, guarded } = {}) {
  const name = normalizeToolName(toolName);
  if (guarded && !guarded.has(name)) return null;
  const hint = hints[name];
  if (!hint) return null;
  const uri = hint.shell ? findVikingUriInShellCommand(input?.command) : findVikingUri(input);
  if (!uri) return null;
  return {
    uri,
    reason: buildGuardMessage(uri, {
      tool: hint.tool,
      example: typeof hint.example === "function" ? hint.example(uri, input) : hint.example,
    }),
  };
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

/** Cursor's deny envelope; its shell hook also wants the reason for the agent. */
export function denyCursorPermission(reason, { agentMessage = false } = {}) {
  const output = { permission: "deny", user_message: reason };
  if (agentMessage) output.agent_message = reason;
  return output;
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
