#!/usr/bin/env node
// OpenViking history import: replays recent Claude Code or Codex sessions from
// this machine into OpenViking, so memories from work done before the plugin
// was installed are there from the first session.
//
// Served at https://docs.openviking.ai/dl/memory-plugin-shared/import-history.mjs
// and https://docs.openviking.net/dl/memory-plugin-shared/import-history.mjs.
// One file, Node.js 18+, no dependencies.
//
//   node import-history.mjs --harness claude            # dry run: what would be imported
//   node import-history.mjs --harness claude --yes      # import it
//   node import-history.mjs --harness claude --list     # list sessions with a preview, to pick some
//   node import-history.mjs --harness claude --session <id>,<id> --yes   # import just those
//
// Nothing is sent without --yes. Run `--help` for every option.
//
// Any other agent: a small converter writes its sessions to a JSONL file, one
// session per line, and this script imports that file the same way:
//
//   node import-history.mjs --harness opencode --input sessions.jsonl
//
//   {"id": "<the agent's session id>", "cwd": "<absolute dir it ran in>",
//    "messages": [{"role": "user"|"assistant", "text": "...", "created_at": "<ISO 8601>"}],
//    "updated_at": "<ISO 8601, optional>", "subagent": false}
//
// id and messages are required; without cwd a session belongs to no project;
// a session with no ISO 8601 time at all is skipped. Other roles are dropped.
// A line that is not such an object, has a user or assistant text that is not
// a string, or whose id maps to the session id of an earlier line, refuses the
// whole file.
//
// What it sends, per session: the user and assistant text only (tool calls and
// tool output are dropped, secrets are redacted), with the original
// timestamps, under the session id `import__<harness>__<native id>` that
// `openviking-server ingest` uses as well, then one commit. The peer is derived
// from the session's working directory the way the memory plugins derive it,
// so imported history and live sessions of the same repository share one peer.
// A session that was already imported, or that the plugin already captured,
// is skipped, so running it twice imports nothing the second time.
//
// The connection is the plugins' own: ~/.openviking/ovcli.conf (or
// OPENVIKING_CLI_CONFIG_FILE), overridden by OPENVIKING_URL / OPENVIKING_API_KEY.
// The API key is never printed.
//
// Source: examples/memory-plugin-shared/import-history.mjs in
// https://github.com/volcengine/OpenViking. The workspace and connection rules
// below are copies of lib/workspace-identity.mjs, lib/workspace-peer.mjs and
// lib/credentials.mjs, kept in step by import-history.test.mjs.

import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, realpathSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, isAbsolute, join, parse, relative, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";

export const VERSION = "1.1.0";
const USER_AGENT = `openviking-import-history/${VERSION}`;
const BATCH_LIMIT = 100;

// The id the live plugin gives a session, so `classify` can tell one it
// already captured. The hook hosts (Cursor, TRAE, ZCode, Kimi Code, CodeBuddy
// Code) take the host's id through safePart (lib/agent-hook-runtime.mjs);
// OpenCode and pi use it as is (deriveHarnessSessionId in lib/session-model.mjs).
const hookLiveId = (prefix) => (id) => `${prefix}${String(id || "unknown").replace(/[^A-Za-z0-9._-]/g, "-")}`;
const codexLiveId = (id) => `cx-${String(id || "unknown").replace(/[^A-Za-z0-9._-]/g, "_")}`;

/** Copy of deriveDshSessionId in dsh-memory-plugin/session-id.mjs. */
function dshLiveId(id) {
  const original = `dsh-${id}`;
  const portable = original.replace(/[<>:"/\\|?*\u0000-\u001F]/g, "_").replace(/[. ]+$/g, (m) => "_".repeat(m.length));
  return portable === original ? original : `${portable}__${createHash("sha256").update(original).digest("hex").slice(0, 12)}`;
}

/**
 * Copy of openClawSessionToOvStorageId in openclaw-plugin/routing/identity-routing.ts,
 * without the sessionKey branch: a converter has only the session id, so a
 * session the plugin stored under sha256(sessionKey) is not recognized.
 */
function openclawLiveId(id) {
  const sid = String(id || "").trim();
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(sid)) return sid.toLowerCase();
  if (/[:<>"\\/|?\u0000-\u001f]/.test(sid)) return createHash("sha256").update(`openclaw-session:${sid}`).digest("hex");
  return sid;
}

// Keyed by the name in the session id `import__<name>__<id>`: the
// `openviking-server ingest` source where one exists (claude_code, codex,
// cursor, opencode, openclaw, hermes), so the two importers skip each other's
// sessions. `native` hosts are read from disk; the rest only with --input.
const HARNESSES = {
  claude_code: { label: "Claude Code", native: true, liveId: (id) => `cc-${id}` },
  codex: { label: "Codex", native: true, liveId: codexLiveId },
  cursor: { label: "Cursor", liveId: hookLiveId("cu-") },
  trae: { label: "TRAE", liveId: hookLiveId("tr-") },
  trae_cn: { label: "TRAE CN", liveId: hookLiveId("trcn-") },
  // The installer sets TRAE CLI up as the Codex plugin run by traecli, so its
  // live sessions are Codex's (cx-). No ingest source reads its history.
  trae_cli: { label: "TRAE CLI", liveId: codexLiveId },
  zcode: { label: "ZCode", liveId: hookLiveId("zc-") },
  kimicode: { label: "Kimi Code", liveId: hookLiveId("kc-") },
  codebuddy: { label: "CodeBuddy Code", liveId: hookLiveId("cb-") },
  // Sub-agent sessions are `oc-<parent>__subagent-<child>` live; --input skips them.
  opencode: { label: "OpenCode", liveId: (id) => `oc-${id}` },
  pi: { label: "pi", liveId: (id) => `pi-${id}` },
  dsh: { label: "dsh", liveId: dshLiveId },
  openclaw: { label: "OpenClaw", liveId: openclawLiveId },
  // Hermes stores a session under its own id, unprefixed. Whether that is the
  // stem of the file `ingest` reads (`<ts>_<id>.jsonl`) is not known.
  hermes: { label: "Hermes", liveId: (id) => String(id) },
};
const HARNESS_ALIASES = {
  claude: "claude_code", "claude-code": "claude_code", claude_code: "claude_code", codex: "codex",
  cursor: "cursor", trae: "trae", "trae-cn": "trae_cn", trae_cn: "trae_cn", "trae-cli": "trae_cli", trae_cli: "trae_cli",
  zcode: "zcode", kimicode: "kimicode", codebuddy: "codebuddy", opencode: "opencode", pi: "pi", dsh: "dsh", openclaw: "openclaw",
  hermes: "hermes",
};
const INPUT_NAMES = "claude, codex, cursor, trae, trae-cn, trae-cli, zcode, kimicode, codebuddy, opencode, pi, dsh, openclaw, hermes";
// The harness name the plugin passes to its config loader, for ov.conf's
// legacy section and the `{harness}` peer template variable. OpenClaw and
// Hermes do not use that loader and name their peers after the agent, not
// the directory, so no peer is derived for them.
const PLUGIN_HARNESS = {
  claude_code: "claude-code", codex: "codex", cursor: "cursor", trae: "trae", trae_cn: "trae-cn", trae_cli: "codex",
  zcode: "zcode", kimicode: "kimicode", codebuddy: "codebuddy", opencode: "opencode", pi: "pi", dsh: "dsh",
};

const DEFAULTS = { since: "14d", maxSessions: 20, skipActive: "2h", maxMessageChars: 20000 };
const LIST_LIMIT = 200;

const USAGE = `Usage: node import-history.mjs --harness claude|codex [options]
       node import-history.mjs --harness <name> --input <file.jsonl> [options]

Imports recent sessions of one agent into OpenViking. Without --yes it only
reports what it would import. Claude Code and Codex history is read where
they keep it; for another agent, a converter writes its sessions to a JSONL
file and --input imports that.

Options:
  --harness <name>        claude (Claude Code) or codex; with --input also cursor, trae,
                          trae-cn, trae-cli, zcode, kimicode, codebuddy, opencode, pi, dsh,
                          openclaw, hermes
  --input <file.jsonl>    import the sessions in this file instead of the agent's history
  --cwd <dir>             the project whose sessions to import (default: the current directory)
  --all-projects          import sessions from every project instead
  --since <age|date>      only sessions active since then: 14d, 36h, 2w, 2026-09-01 or all (default: ${DEFAULTS.since})
  --max-sessions <n>      at most this many, newest first (default: ${DEFAULTS.maxSessions})
  --skip-active <age>     skip sessions active within this long, which may still be running (default: ${DEFAULTS.skipActive})
  --peer <id|none>        write under this peer instead of the derived one
  --history-dir <dir>     read sessions from here (default: ~/.claude/projects or ~/.codex/sessions)
  --max-message-chars <n> cut longer messages to this many characters (default: ${DEFAULTS.maxMessageChars})
  --list                  list the sessions in scope, with their ids and a preview of the
                          first and last request, so some can be picked; never writes. It
                          uses the same --cwd / --all-projects and --since scope, but lists
                          up to ${LIST_LIMIT} sessions unless --max-sessions is given, and says
                          how many it left out and which option widens the scope.
  --session <id>[,<id>]   import only these sessions; comma-separated, and repeatable. An id
                          is the one --list prints, the import id, or a prefix of 8 or more
                          characters (an exact id always wins over a prefix). Picked sessions
                          ignore --cwd, --since and --max-sessions; sessions still active are
                          skipped. An id that matches nothing importable, or more than one
                          session, refuses the run.
  --dry-run               report only; never write (the default without --yes)
  --yes                   import
  --wait                  after importing, wait up to 120s for memory extraction to finish
  --json                  print the result as JSON
  -h, --help              this text

Input file (--input): one session per line, a JSON object:
  {"id": "<the agent's own session id>", "cwd": "<absolute dir the session ran in>",
   "messages": [{"role": "user", "text": "...", "created_at": "2026-10-01T09:30:00Z"}, ...],
   "updated_at": "<ISO 8601, optional>", "subagent": false}
  id and messages are required. A session without cwd belongs to no project, so
  only --all-projects imports it; a cwd must be absolute. Give each message its
  created_at (ISO 8601): a session with no such time at all is skipped. Roles
  other than user and assistant are dropped and counted; subagent: true sessions
  are skipped. A line that is not such an object, has a user or assistant
  message whose text is not a string, or whose id repeats an earlier one (also
  once reduced to the letters, digits and ._@- a session id keeps) refuses the
  whole file, with its line number. Blank lines are ignored. --input cannot be
  combined with --history-dir.
`;

// ---------------------------------------------------------------------------
// Arguments
// ---------------------------------------------------------------------------

export function parseArgs(argv) {
  const opts = {
    harness: "", cwd: "", allProjects: false, since: DEFAULTS.since, maxSessions: DEFAULTS.maxSessions,
    skipActive: DEFAULTS.skipActive, peer: "", historyDir: "", input: "", maxMessageChars: DEFAULTS.maxMessageChars,
    dryRun: false, yes: false, wait: false, json: false, help: false, list: false, sessions: [],
  };
  const valueOf = (i, name) => {
    const value = argv[i + 1];
    if (value === undefined || value.startsWith("--")) throw new UsageError(`${name} needs a value`);
    return value;
  };
  for (let i = 0; i < argv.length; i += 1) {
    let arg = argv[i];
    let inline;
    if (arg.startsWith("--") && arg.includes("=")) {
      inline = arg.slice(arg.indexOf("=") + 1);
      arg = arg.slice(0, arg.indexOf("="));
    }
    const take = () => {
      if (inline !== undefined) return inline;
      const value = valueOf(i, arg);
      i += 1;
      return value;
    };
    switch (arg) {
      case "--harness": opts.harness = take(); break;
      case "--cwd": opts.cwd = take(); break;
      case "--all-projects": opts.allProjects = true; break;
      case "--since": opts.since = take(); break;
      case "--max-sessions": opts.maxSessions = positiveInt(take(), arg); opts.maxSessionsGiven = true; break;
      case "--skip-active": opts.skipActive = take(); break;
      case "--peer": opts.peer = take(); break;
      case "--history-dir": opts.historyDir = take(); break;
      case "--input": opts.input = take(); break;
      case "--max-message-chars": opts.maxMessageChars = positiveInt(take(), arg); break;
      case "--list": opts.list = true; break;
      case "--session": case "--sessions": {
        const ids = take().split(",").map((id) => id.trim()).filter(Boolean);
        if (!ids.length) throw new UsageError(`${arg} needs at least one session id`);
        opts.sessions.push(...ids);
        break;
      }
      case "--dry-run": opts.dryRun = true; break;
      case "--yes": case "-y": opts.yes = true; break;
      case "--wait": opts.wait = true; break;
      case "--json": opts.json = true; break;
      case "--help": case "-h": opts.help = true; break;
      default: throw new UsageError(`unknown option ${argv[i]}`);
    }
  }
  if (opts.list && opts.yes) throw new UsageError("--list only lists; drop --yes, or import the picked sessions with --session");
  return opts;
}

class UsageError extends Error {}

function positiveInt(value, name) {
  const n = Number(value);
  if (!Number.isInteger(n) || n < 1) throw new UsageError(`${name} must be a positive integer`);
  return n;
}

const UNIT_MS = { m: 60_000, h: 3_600_000, d: 86_400_000, w: 7 * 86_400_000 };

/** `14d` → a cutoff in ms since the epoch; `all` → 0. */
export function parseSince(value, now = Date.now()) {
  const raw = String(value || "").trim().toLowerCase();
  if (!raw || raw === "all") return 0;
  const age = /^(\d+)\s*([mhdw])$/.exec(raw);
  if (age) return now - Number(age[1]) * UNIT_MS[age[2]];
  const date = Date.parse(raw);
  if (Number.isFinite(date)) return date;
  throw new UsageError(`--since: cannot read ${JSON.stringify(value)}; use 14d, 36h, 2w, a date or all`);
}

export function parseAge(value, name = "--skip-active") {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "0" || raw === "none") return 0;
  const age = /^(\d+)\s*([mhdw])$/.exec(raw);
  if (!age) throw new UsageError(`${name}: cannot read ${JSON.stringify(value)}; use 30m, 2h or 0`);
  return Number(age[1]) * UNIT_MS[age[2]];
}

// ---------------------------------------------------------------------------
// Workspace identity (copy of lib/workspace-identity.mjs, uncached)
// ---------------------------------------------------------------------------

const CONFIG_DIR_NAME = ".openviking";
const TEAM_FILE = "config.json";
const LOCAL_FILE = "config.local.json";
const MAX_PEER_ID_LENGTH = 100;

function shortHash(value) {
  return createHash("sha256").update(String(value)).digest("hex").slice(0, 12);
}

export function legacySanitize(value) {
  return String(value || "").replace(/[^A-Za-z0-9]/g, "-");
}

export function sanitizePeerId(value) {
  const raw = String(value || "").trim();
  let cleaned = raw.replace(/[^a-zA-Z0-9_.@-]+/g, "-").replace(/-{2,}/g, "-").replace(/^[-.]+|[-.]+$/g, "");
  if (!cleaned) return "";
  const at = cleaned.indexOf("@");
  if (at !== -1) cleaned = cleaned.slice(0, at + 1) + cleaned.slice(at + 1).replace(/@/g, "-");
  if (cleaned.startsWith("ext-")) cleaned = `x-${cleaned}`;
  if (cleaned === "__self") cleaned = "self";
  if (cleaned === "." || cleaned === "..") return "";
  if (cleaned.length > MAX_PEER_ID_LENGTH) {
    cleaned = `${cleaned.slice(0, MAX_PEER_ID_LENGTH - 13).replace(/[-.]+$/, "")}-${shortHash(raw)}`;
  }
  return cleaned;
}

export function normalizeGitRemote(url) {
  const raw = String(url || "").trim();
  if (!raw) return "";
  if (/^[A-Za-z]:[\\/]/.test(raw)) return "";
  let host = "";
  let path = "";
  const scp = /^(?:[^@/\\]+@)?([^:/\\]+):(?!\/)(.+)$/.exec(raw);
  if (scp && !/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(raw)) {
    host = scp[1];
    path = scp[2];
  } else {
    let parsed;
    try {
      parsed = new URL(raw);
    } catch {
      return "";
    }
    if (parsed.protocol === "file:" || !parsed.hostname) return "";
    host = parsed.hostname;
    path = parsed.pathname;
  }
  host = host.toLowerCase().replace(/^\[|\]$/g, "");
  path = path.replace(/^\/+/, "").replace(/\/+$/, "").replace(/\.git$/i, "").toLowerCase();
  if (!host || !path) return "";
  return `${host}/${path}`;
}

function readFileOrEmpty(path) {
  try {
    return readFileSync(path, "utf-8");
  } catch {
    return "";
  }
}

function readGitRemoteUrl(commonDir, remote = "origin") {
  const text = readFileOrEmpty(join(commonDir, "config"));
  if (!text) return "";
  let inSection = false;
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith(";")) continue;
    const section = /^\[([^\]]+)\]/.exec(trimmed);
    if (section) {
      const quoted = /^remote\s+"(.*)"$/i.exec(section[1].trim());
      inSection = Boolean(quoted && quoted[1] === remote);
      continue;
    }
    if (!inSection) continue;
    const pair = /^url\s*=\s*(.*)$/i.exec(trimmed);
    if (pair) return pair[1].split(/[#;]/)[0].trim().replace(/^["']|["']$/g, "");
  }
  return "";
}

function resolveGitDir(root) {
  const dotGit = join(root, ".git");
  let stat;
  try {
    stat = statSync(dotGit);
  } catch {
    return null;
  }
  if (stat.isDirectory()) return { gitDir: dotGit, commonDir: dotGit, kind: "repo" };
  if (!stat.isFile()) return null;
  const pointer = /^gitdir:\s*(.+)$/m.exec(readFileOrEmpty(dotGit));
  if (!pointer) return null;
  const gitDir = resolve(root, pointer[1].trim());
  const commonRef = readFileOrEmpty(join(gitDir, "commondir")).trim();
  const commonDir = commonRef ? (isAbsolute(commonRef) ? commonRef : resolve(gitDir, commonRef)) : gitDir;
  const kind = /[/\\]\.git[/\\]modules[/\\]/.test(`${commonDir}${sep}`) ? "submodule" : (commonRef ? "worktree" : "repo");
  return { gitDir, commonDir, kind };
}

function hasWorkspaceMarker(dir) {
  for (const name of [TEAM_FILE, LOCAL_FILE]) {
    try {
      if (statSync(join(dir, CONFIG_DIR_NAME, name)).isFile()) return true;
    } catch { /* not marked here */ }
  }
  return false;
}

const NO_ROOT = Object.freeze({ root: "", rootKind: "", git: null, gitRoot: "" });

export function findWorkspaceRoot(cwd, env = process.env) {
  const start = String(cwd || "").trim();
  if (!start) return NO_ROOT;
  let absolute;
  try {
    absolute = resolve(start);
  } catch {
    return NO_ROOT;
  }
  const home = String(env.HOME || "").trim() || homedir();
  let current;
  let stopAt;
  try {
    current = realpathSync(absolute);
    stopAt = realpathSync(home);
  } catch {
    current = absolute;
    stopAt = home;
  }
  const filesystemRoot = parse(current).root;
  if (current === stopAt || current === filesystemRoot) return NO_ROOT;
  let root = "";
  let rootKind = "";
  while (current && current !== filesystemRoot && current !== stopAt) {
    const git = resolveGitDir(current);
    if (git) return { root: root || current, rootKind: rootKind || "git", git, gitRoot: current };
    if (!root && hasWorkspaceMarker(current)) {
      root = current;
      rootKind = "config";
    }
    const parentDir = dirname(current);
    if (parentDir === current) break;
    current = parentDir;
  }
  return root ? { root, rootKind, git: null, gitRoot: "" } : NO_ROOT;
}

export function workspaceVars(cwd, env = process.env) {
  const key = String(cwd || "");
  const { root, git, gitRoot } = findWorkspaceRoot(key, env);
  const remote = git ? normalizeGitRemote(readGitRemoteUrl(git.commonDir)) : "";
  return {
    root,
    identity: { remote, isGit: Boolean(git), gitCommonDir: git?.commonDir || "" },
    vars: {
      git_remote: sanitizePeerId(remote),
      git_root: git ? legacySanitize(gitRoot) : "",
      cwd: legacySanitize(key),
      dir: root ? sanitizePeerId(root.split(/[/\\]/).filter(Boolean).pop() || "") : "",
    },
  };
}

// ---------------------------------------------------------------------------
// Peer (copy of lib/workspace-peer.mjs and the peer knobs the loaders read)
// ---------------------------------------------------------------------------

const PEER_SOURCE_PRESETS = { git: ["{git_remote}", "{git_root}"], cwd: ["{cwd}"], none: [] };
const TRUE_WORDS = ["1", "true", "yes", "on"];
const FALSE_WORDS = ["0", "false", "no", "off"];

function peerSourceTemplates(source) {
  if (Array.isArray(source)) return source.map(String).filter(Boolean);
  const raw = String(source ?? "").trim();
  if (!raw) return PEER_SOURCE_PRESETS.git;
  if (Object.hasOwn(PEER_SOURCE_PRESETS, raw)) return PEER_SOURCE_PRESETS[raw];
  if (raw.includes("{")) return [raw];
  return PEER_SOURCE_PRESETS.git;
}

function renderPeerTemplate(template, vars) {
  const text = String(template || "");
  if (!text) return "";
  let empty = false;
  const rendered = text.replace(/\{([a-z_]+)\}/g, (match, name) => {
    if (!Object.hasOwn(vars, name)) {
      empty = true;
      return "";
    }
    const value = String(vars[name] ?? "");
    if (!value) empty = true;
    return value;
  });
  return empty ? "" : rendered;
}

function coerceBool(raw) {
  if (typeof raw === "boolean") return raw;
  if (raw === undefined || raw === null) return undefined;
  const word = String(raw).trim().toLowerCase();
  if (TRUE_WORDS.includes(word)) return true;
  if (FALSE_WORDS.includes(word)) return false;
  return undefined;
}

function coerceTemplate(raw) {
  if (Array.isArray(raw)) {
    const items = raw.filter((item) => typeof item === "string").map((item) => item.trim()).filter(Boolean);
    return items.length ? items : undefined;
  }
  if (raw === undefined || raw === null) return undefined;
  const text = String(raw).trim();
  return text || undefined;
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return null;
  }
}

/** ovcli.conf's `plugin` keys as one harness sees them: shared, then `plugin.<harness>`. */
function pluginSection(cliFile, harness) {
  const plugin = isPlainObject(cliFile?.plugin) ? cliFile.plugin : {};
  const merged = {};
  for (const [key, value] of Object.entries(plugin)) if (!isPlainObject(value)) merged[key] = value;
  const key = sectionKey(harness);
  for (const candidate of [key, key.replace(/_/g, "-")]) {
    if (candidate && isPlainObject(plugin[candidate])) Object.assign(merged, plugin[candidate]);
  }
  return merged;
}

function sectionKey(harness) {
  return String(harness || "").trim().toLowerCase().replace(/-/g, "_");
}

const MAX_CONFIG_BYTES = 64 * 1024;
const CONFIG_VERSION = 1;

/** A workspace or registry file as lib/workspace-config.mjs accepts it, or null. */
function readWorkspaceFile(path, root = "") {
  try {
    const stat = statSync(path);
    if (!stat.isFile() || stat.size > MAX_CONFIG_BYTES) return null;
    if (root) {
      const rel = relative(realpathSync(root), realpathSync(path));
      if (!rel || rel.startsWith("..") || isAbsolute(rel)) return null;
    }
  } catch {
    return null;
  }
  const data = readJson(path);
  return isPlainObject(data) && data.version === CONFIG_VERSION ? data : null;
}

function registryEntry(root, identity, env) {
  const home = String(env.OPENVIKING_HOME || "").trim();
  const base = home ? home.replace(/^~(?=$|\/)/, String(env.HOME || "").trim() || homedir()) : join(String(env.HOME || "").trim() || homedir(), ".openviking");
  const key = identity.remote ? `remote:${identity.remote}` : (identity.isGit ? `git:${identity.gitCommonDir || ""}` : "path");
  const source = key === "path" ? root : key;
  const label = key.startsWith("remote:") ? key.split("/").pop() : basename(root);
  const readable = String(label || "").replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);
  const digest = createHash("sha256").update(source).digest("hex").slice(0, 12);
  const entry = readWorkspaceFile(join(base, "workspaces", `${readable ? `${readable}-` : ""}${digest}.json`));
  if (!entry) return null;
  const stored = String(entry.identity || "");
  return stored && stored !== key ? null : entry;
}

/**
 * The peer knobs (`peer.id`, `peer.source`) of the workspace layers: the
 * committed file, the local one, then the machine registry's entry.
 */
function workspacePeerSettings(root, identity, env) {
  const out = {};
  if (!root) return out;
  const layers = [readWorkspaceFile(join(root, CONFIG_DIR_NAME, TEAM_FILE), root), readWorkspaceFile(join(root, CONFIG_DIR_NAME, LOCAL_FILE), root)];
  const entry = registryEntry(root, identity, env);
  if (entry) layers.push(entry.settings, { peer: entry.peer });
  for (const data of layers) {
    const peer = isPlainObject(data) && isPlainObject(data.peer) ? data.peer : null;
    if (!peer) continue;
    if (peer.id !== undefined) out.peerId = peer.id;
    if (peer.source !== undefined) out.peerSource = peer.source;
  }
  return out;
}

/**
 * The peer the plugin would write a session in `cwd` under, and the rule that
 * produced it. Resolution follows the loaders: the environment, then the
 * workspace files over ovcli.conf's `plugin` keys, then ovcli.conf's
 * `actor_peer_id`, then ov.conf's harness section; with no peer named, the
 * `peer.source` templates (by default the repository's remote, then its root).
 */
export function resolvePeer({ cwd, harness, connection, env = process.env }) {
  const pluginHarness = PLUGIN_HARNESS[harness] || harness;
  const cliFile = connection?.cliFile || {};
  const legacy = isPlainObject(connection?.ovFile?.[sectionKey(pluginHarness)])
    ? connection.ovFile[sectionKey(pluginHarness)] : {};
  const { root, identity, vars } = workspaceVars(cwd, env);
  const layered = { ...pluginSection(cliFile, pluginHarness), ...workspacePeerSettings(root, identity, env) };

  const str = (value) => (typeof value === "string" ? value.trim() : (value === undefined || value === null ? "" : String(value).trim()));
  // Knobs: env over ovcli.conf (workspace files merged in) over ov.conf.
  const pick = (name, aliases, envName, coerce) => {
    let value;
    for (const layer of [legacy, layered]) {
      for (const key of [...aliases, name]) {
        if (!Object.hasOwn(layer, key) || layer[key] === undefined) continue;
        const coerced = coerce(layer[key]);
        if (coerced !== undefined) value = coerced;
      }
    }
    const raw = env[envName];
    if (raw !== undefined && raw !== null && String(raw) !== "") {
      const coerced = coerce(raw);
      if (coerced !== undefined) value = coerced;
    }
    return value;
  };
  const peerSource = pick("peerSource", [], "OPENVIKING_PEER_SOURCE", coerceTemplate);
  const workspacePeer = pick("workspacePeer", [], "OPENVIKING_WORKSPACE_PEER", coerceBool);

  // resolvePluginPeerId: the environment, then a peer some file named, then
  // ovcli.conf's actor peer, then ov.conf's harness section. A peer the
  // environment names hides the files' one even where the environment itself
  // is not read, as the loaders' knob resolution does.
  const lastNamed = (layer) => {
    let value = "";
    for (const key of ["peer_id", "peerId"]) if (Object.hasOwn(layer, key) && str(layer[key])) value = str(layer[key]);
    return value;
  };
  const envPeer = str(env.OPENVIKING_PEER_ID);
  const layeredPeer = lastNamed(layered);
  const legacyPeer = lastNamed(legacy);
  let named = "";
  let origin = "";
  if (connection?.credentialSource !== "ovcli" && envPeer) {
    named = envPeer;
    origin = "OPENVIKING_PEER_ID";
  } else if (!envPeer && layeredPeer) {
    named = layeredPeer;
    origin = "peer.id";
  } else if (str(connection?.peerId)) {
    named = str(connection.peerId);
    origin = "actor_peer_id";
  } else if (!envPeer && legacyPeer) {
    named = legacyPeer;
    origin = "ov.conf";
  }
  if (named) return { peerId: named, origin };

  // resolveEffectivePeerId
  if (workspacePeer === false) return { peerId: "", origin: "disabled" };
  const templates = peerSourceTemplates(peerSource);
  const allVars = { ...vars, harness: sanitizePeerId(pluginHarness) };
  for (const template of templates) {
    const peerId = renderPeerTemplate(template, allVars);
    if (peerId) return { peerId, origin: template };
  }
  return { peerId: "", origin: "none" };
}

// ---------------------------------------------------------------------------
// Connection (copy of resolveConnection in lib/credentials.mjs)
// ---------------------------------------------------------------------------

const CREDENTIAL_ENV_VARS = [
  "OPENVIKING_URL", "OPENVIKING_BASE_URL", "OPENVIKING_MCP_URL", "OPENVIKING_BEARER_TOKEN",
  "OPENVIKING_API_KEY", "OPENVIKING_ACCOUNT", "OPENVIKING_USER", "OPENVIKING_PEER_ID",
];

function expandHome(value, env) {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) return "";
  const home = String(env.HOME || "").trim() || homedir();
  if (raw === "~") return home;
  if (raw.startsWith("~/")) return resolve(join(home, raw.slice(2)));
  return resolve(raw);
}

function looksLikeOvcli(obj) {
  if (!isPlainObject(obj) || isPlainObject(obj.server)) return false;
  return ["url", "api_key", "account", "account_id", "user", "user_id", "actor_peer_id"].some((k) => typeof obj[k] === "string");
}

function hasCredentialFields(obj) {
  return isPlainObject(obj)
    && ["url", "api_key", "account", "account_id", "user", "user_id", "actor_peer_id", "peer_id"].some((k) => typeof obj[k] === "string");
}

export function resolveConnection(harness, { env = process.env } = {}) {
  const home = String(env.HOME || "").trim() || homedir();
  const s = (value, fallback = "") => (typeof value === "string" && value.trim() ? value.trim() : fallback);
  const cliPathCandidate = expandHome(env.OPENVIKING_CLI_CONFIG_FILE, env) || join(home, ".openviking", "ovcli.conf");
  const ovPathCandidate = expandHome(env.OPENVIKING_CONFIG_FILE, env) || join(home, ".openviking", "ov.conf");
  let cliFile = readJson(cliPathCandidate);
  let cliPath = cliFile ? cliPathCandidate : "";
  let ovFile = readJson(ovPathCandidate);
  let ovPath = ovFile ? ovPathCandidate : "";
  if (s(env.OPENVIKING_CONFIG_FILE) && !s(env.OPENVIKING_CLI_CONFIG_FILE) && looksLikeOvcli(ovFile)) {
    cliFile = ovFile;
    cliPath = ovPath;
    ovFile = null;
    ovPath = "";
  }
  cliFile = isPlainObject(cliFile) ? cliFile : {};
  ovFile = isPlainObject(ovFile) ? ovFile : {};

  const modeRaw = s(env.OPENVIKING_CREDENTIAL_SOURCE, s(env.OPENVIKING_CREDENTIALS_SOURCE, "auto")).toLowerCase();
  const mode = ["env", "environment"].includes(modeRaw) ? "env"
    : (["cli", "ovcli", "file", "config"].includes(modeRaw) ? "cli" : "auto");
  const envHasCredentials = CREDENTIAL_ENV_VARS.some((name) => s(env[name]));
  const pinned = mode === "cli" || (mode === "auto" && !envHasCredentials && Boolean(cliPath) && hasCredentialFields(cliFile));
  const fromFiles = mode !== "env";
  const plugin = pluginSection(cliFile, harness);
  const section = isPlainObject(ovFile[sectionKey(harness)]) ? ovFile[sectionKey(harness)] : {};
  const server = isPlainObject(ovFile.server) ? ovFile.server : {};
  const scalar = (value) => (value === undefined || value === null ? "" : String(value).trim());
  const first = (layers) => layers.find(([value]) => value) || ["", "none", ""];

  const [apiKey, apiKeySource, credentialPath] = first([
    ...(pinned ? [] : [[s(env.OPENVIKING_BEARER_TOKEN, s(env.OPENVIKING_API_KEY)), "env", ""]]),
    ...(fromFiles ? [
      [s(cliFile.api_key), "ovcli", cliPath],
      [scalar(plugin.apiKey), "ovcli", cliPath],
      [scalar(section.apiKey), "ov", ovPath],
    ] : []),
    ...((pinned || !fromFiles) ? [] : [[s(server.root_api_key), "ov", ovPath]]),
  ]);
  const identity = (envName, cliValue, knob) => first([
    ...(pinned ? [] : [[s(env[envName]), "env"]]),
    ...(fromFiles ? [[cliValue, "ovcli"], [scalar(plugin[knob]), "ovcli"]] : []),
    ...((pinned || !fromFiles) ? [] : [[s(section[knob]), "ov"]]),
  ])[0];
  const account = identity("OPENVIKING_ACCOUNT", s(cliFile.account, s(cliFile.account_id)), "accountId");
  const user = identity("OPENVIKING_USER", s(cliFile.user, s(cliFile.user_id)), "userId");
  const peerId = first([
    ...(pinned ? [] : [[s(env.OPENVIKING_PEER_ID), "env"]]),
    ...(fromFiles ? [[s(cliFile.actor_peer_id, s(cliFile.peer_id)), "ovcli"]] : []),
    ...((pinned || !fromFiles) ? [] : [[s(section.peerId, s(section.peer_id)), "ov"]]),
  ])[0];
  const authModeOf = (value) => (["trusted", "api_key"].includes(s(value).toLowerCase()) ? s(value).toLowerCase() : "");
  const authMode = authModeOf(env.OPENVIKING_AUTH_MODE)
    || (fromFiles && (authModeOf(plugin.authMode) || authModeOf(plugin.auth_mode) || authModeOf(section.authMode)
      || authModeOf(section.auth_mode) || authModeOf(server.auth_mode)))
    || ((account || user) ? "trusted" : "api_key");
  const envUrl = pinned ? "" : s(env.OPENVIKING_URL, s(env.OPENVIKING_BASE_URL));
  const fileUrl = fromFiles ? (s(cliFile.url) || s(server.url)) : "";
  const serverHost = s(fromFiles ? server.host : "", "127.0.0.1").replace("0.0.0.0", "127.0.0.1");
  const serverPort = Number.isFinite(Number(fromFiles ? server.port : undefined)) ? Math.floor(Number(server.port)) : 1933;
  const baseUrl = (envUrl || fileUrl).replace(/\/+$/, "") || `http://${serverHost}:${serverPort}`;

  return {
    cliFile, ovFile, cliPath, ovPath,
    credentialSource: pinned ? "ovcli" : ((mode === "env" || envHasCredentials) ? "env" : "auto"),
    baseUrl, apiKey, apiKeySource, credentialPath, account, user, peerId, authMode,
    sendIdentityHeaders: authMode === "trusted",
  };
}

// ---------------------------------------------------------------------------
// Redaction and text cleanup
// ---------------------------------------------------------------------------

const REDACTED = "[REDACTED]";
const SECRET_NAME = "(?:api[_-]?key|apikey|access[_-]?key|secret(?:[_-]?key)?|client[_-]?secret|token|auth[_-]?token|bearer[_-]?token|password|passwd|pwd)";

const REDACTIONS = [
  // PEM private keys, whole.
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g, () => `-----BEGIN PRIVATE KEY----- ${REDACTED} -----END PRIVATE KEY-----`],
  // Command-line flags: --api-key VALUE, --api-key=VALUE, --token "VALUE".
  [new RegExp(`(--${SECRET_NAME})(\\s*=\\s*|\\s+)("[^"]*"|'[^']*'|[^\\s"'\`]+)`, "gi"), (m, flag, sep) => `${flag}${sep}${REDACTED}`],
  // HTTP authorization headers.
  [/\b(Authorization\s*:\s*)(Bearer|Basic|Token)\s+[^\s"'`]+/gi, (m, head, scheme) => `${head}${scheme} ${REDACTED}`],
  [/\b(Bearer\s+)[A-Za-z0-9._~+/=-]{16,}/g, (m, head) => `${head}${REDACTED}`],
  // Environment assignments: OPENVIKING_API_KEY=..., export GITHUB_TOKEN="...".
  [/\b([A-Z][A-Z0-9_]*(?:API_KEY|APIKEY|TOKEN|SECRET|SECRET_KEY|PASSWORD|PASSWD|ACCESS_KEY)[A-Z0-9_]*)(\s*=\s*)("[^"]*"|'[^']*'|[^\s"'`]+)/g,
    (m, name, sep) => `${name}${sep}${REDACTED}`],
  // key: value / "key": "value" in configs and JSON.
  [new RegExp(`(["']?\\b[A-Za-z0-9_.-]*${SECRET_NAME}["']?\\s*[:=]\\s*)("[^"\\n]{4,}"|'[^'\\n]{4,}'|[^\\s"',;}{]{6,})`, "gi"),
    (m, head, value) => `${head}${/^["']/.test(value) ? `${value[0]}${REDACTED}${value[0]}` : REDACTED}`],
  // Well-known token shapes, wherever they appear.
  [/\b(?:sk|pk|rk)-(?:ant-|proj-|live-|test-)?[A-Za-z0-9_-]{16,}/g, () => REDACTED],
  [/\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b/g, () => REDACTED],
  [/\bgithub_pat_[A-Za-z0-9_]{20,}\b/g, () => REDACTED],
  [/\bglpat-[A-Za-z0-9_-]{20,}\b/g, () => REDACTED],
  [/\bxox[abposr]-[A-Za-z0-9-]{10,}\b/g, () => REDACTED],
  [/\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g, () => REDACTED],
  [/\bAIza[0-9A-Za-z_-]{35}\b/g, () => REDACTED],
  [/\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}/g, () => REDACTED],
];

/** Redact secrets: the configured key wherever it appears, then the patterns above. */
export function redactSecrets(text, knownSecrets = []) {
  let value = String(text || "");
  for (const secret of knownSecrets) {
    if (secret && secret.length >= 6) value = value.split(secret).join(REDACTED);
  }
  for (const [pattern, replace] of REDACTIONS) value = value.replace(pattern, replace);
  return value;
}

// Blocks a host or a plugin adds to the conversation that are not the user's
// words: injected recall, host notes, slash-command wrappers, Codex's
// environment and instruction preambles.
const NOISE_BLOCKS = [
  /<openviking-context\b[^>]*>[\s\S]*?<\/openviking-context>/gi,
  /<relevant-memor(?:y|ies)\b[^>]*>[\s\S]*?<\/relevant-memor(?:y|ies)>/gi,
  /<system-reminder\b[^>]*>[\s\S]*?<\/system-reminder>/gi,
  /<local-command-(?:stdout|stderr|caveat)\b[^>]*>[\s\S]*?<\/local-command-(?:stdout|stderr|caveat)>/gi,
  /<command-(?:name|message|args)\b[^>]*>[\s\S]*?<\/command-(?:name|message|args)>/gi,
  /<environment_context\b[^>]*>[\s\S]*?<\/environment_context>/gi,
  /<user_instructions\b[^>]*>[\s\S]*?<\/user_instructions>/gi,
  /<turn_aborted\b[^>]*>[\s\S]*?<\/turn_aborted>/gi,
  /<user_shell_command\b[^>]*>[\s\S]*?<\/user_shell_command>/gi,
  /^# AGENTS\.md instructions for [^\n]*\n\s*<INSTRUCTIONS>[\s\S]*?(?:<\/INSTRUCTIONS>|$(?![\s\S]))/gm,
  /^Caveat: The messages below were generated by the user while running local commands\.[^\n]*$/gm,
  /^\[Request interrupted by user[^\]]*\]$/gm,
];

export function cleanText(text) {
  let value = String(text || "").replace(/\u0000/g, "");
  for (const pattern of NOISE_BLOCKS) value = value.replace(pattern, " ");
  return value.replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

// Sessions the memory plugins start themselves, which are no one's work.
const UTILITY_PROMPTS = [/^You are a memory relevance compressor utility\./];

// ---------------------------------------------------------------------------
// History readers
// ---------------------------------------------------------------------------

function readJsonl(path) {
  const records = [];
  let text;
  try {
    text = readFileSync(path, "utf-8");
  } catch {
    return records;
  }
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    try {
      records.push(JSON.parse(line));
    } catch { /* a torn last line, or not JSON */ }
  }
  return records;
}

function claudeText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((block) => block && block.type === "text" && typeof block.text === "string")
    .map((block) => block.text)
    .join("\n");
}

/** One Claude Code transcript → { nativeId, cwd, messages[{role,text,createdAt}] }. */
export function parseClaudeSession(path) {
  const nativeId = basename(path, ".jsonl");
  let cwd = "";
  const messages = [];
  for (const record of readJsonl(path)) {
    if (!cwd && typeof record?.cwd === "string") cwd = record.cwd;
    if (record?.type !== "user" && record?.type !== "assistant") continue;
    if (record.isSidechain || record.isMeta || record.isCompactSummary || record.isApiErrorMessage) continue;
    const message = record.message;
    if (!isPlainObject(message)) continue;
    const role = message.role;
    if (role !== "user" && role !== "assistant") continue;
    if (role === "assistant" && message.model === "<synthetic>") continue;
    const text = cleanText(claudeText(message.content));
    if (!text) continue;
    messages.push({ role, text, createdAt: typeof record.timestamp === "string" ? record.timestamp : "" });
  }
  return { nativeId, liveId: nativeId, cwd, messages, utility: false };
}

const UUID_RE = /[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/g;

/** The id `openviking-server ingest` gives a rollout file: forks keep their own. */
export function codexNativeId(metaId, path) {
  const stem = basename(path, ".jsonl");
  const base = metaId || stem;
  const uuids = stem.match(UUID_RE) || [];
  const extras = uuids.filter((u) => u !== base);
  return uuids.length >= 2 && extras.length ? `${base}_${extras[extras.length - 1]}` : base;
}

/** One Codex rollout → { nativeId, liveId, cwd, messages }. */
export function parseCodexSession(path) {
  let meta = null;
  const messages = [];
  for (const record of readJsonl(path)) {
    if (!meta && record?.type === "session_meta" && isPlainObject(record.payload)) meta = record.payload;
    if (record?.type !== "response_item") continue;
    const payload = record.payload;
    if (!isPlainObject(payload) || payload.type !== "message") continue;
    const role = payload.role;
    if (role !== "user" && role !== "assistant") continue;
    const text = cleanText((Array.isArray(payload.content) ? payload.content : [])
      .filter((block) => block && (block.type === "input_text" || block.type === "output_text") && typeof block.text === "string")
      .map((block) => block.text)
      .join("\n"));
    if (!text) continue;
    messages.push({ role, text, createdAt: typeof record.timestamp === "string" ? record.timestamp : "" });
  }
  const metaId = typeof meta?.id === "string" ? meta.id : "";
  // A sub-agent thread is part of its parent's work, not a session of its own.
  const source = meta?.source;
  const subagent = isPlainObject(source) && Object.keys(source).some((key) => /sub_?agent/i.test(key));
  return {
    nativeId: codexNativeId(metaId, path),
    liveId: metaId || basename(path, ".jsonl"),
    cwd: typeof meta?.cwd === "string" ? meta.cwd : "",
    messages,
    utility: subagent,
  };
}

function listFiles(dir, match, depth) {
  const out = [];
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory() && depth > 0) out.push(...listFiles(path, match, depth - 1));
    else if (entry.isFile() && match(entry.name)) out.push(path);
  }
  return out;
}

export function historyRoot(harness, env = process.env, override = "") {
  if (override) return expandHome(override, env);
  const home = String(env.HOME || "").trim() || homedir();
  if (harness === "claude_code") return join(expandHome(env.CLAUDE_CONFIG_DIR, env) || join(home, ".claude"), "projects");
  return join(expandHome(env.CODEX_HOME, env) || join(home, ".codex"), "sessions");
}

/** Session files with their last-write time, before any parsing. */
export function discoverSessionFiles(harness, root) {
  const files = harness === "claude_code"
    ? listFiles(root, (name) => name.endsWith(".jsonl"), 1).filter((path) => dirname(dirname(path)) === root)
    : listFiles(root, (name) => name.startsWith("rollout-") && name.endsWith(".jsonl"), 4);
  return files.map((path) => {
    let mtimeMs = 0;
    try {
      mtimeMs = statSync(path).mtimeMs;
    } catch { /* gone since the listing */ }
    return { path, mtimeMs };
  });
}

class InputError extends Error {
  constructor(message, problems = []) {
    super(message);
    this.problems = problems;
  }
}

const MAX_REPORTED_PROBLEMS = 10;
// ISO 8601 only: Date.parse reads almost anything ("1" is 2001-01-01), and the
// string is sent to the server as is.
const ISO_TIME = /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$/;
const parseableTime = (value) => (typeof value === "string" && ISO_TIME.test(value.trim()) && Number.isFinite(Date.parse(value.trim())) ? value.trim() : "");

/**
 * The sessions of an --input file (format in USAGE), shaped like the readers'
 * output. One bad line refuses the whole file: it is a converter bug, and a
 * half import would hide it.
 */
export function readInputSessions(path) {
  let text;
  try {
    text = readFileSync(path, "utf-8");
  } catch (error) {
    throw new InputError(`cannot read --input ${path} (${error?.code || error?.message || error})`);
  }
  const sessions = [];
  const problems = [];
  const seen = new Map();
  let droppedMessages = 0;
  text.replace(/^﻿/, "").split("\n").forEach((raw, index) => {
    const line = index + 1;
    if (!raw.trim()) return;
    const problem = (reason) => { problems.push({ line, reason }); };
    let record;
    try {
      record = JSON.parse(raw);
    } catch {
      return problem("not valid JSON");
    }
    if (!isPlainObject(record)) return problem("not a JSON object");
    const id = typeof record.id === "string" ? record.id.trim() : "";
    if (!id) return problem('"id" must be a non-empty string');
    if (!Array.isArray(record.messages)) return problem('"messages" must be an array');
    const cwd = record.cwd ?? "";
    if (typeof cwd !== "string" || (cwd && !isAbsolute(cwd))) return problem('"cwd" must be an absolute directory');
    // Keyed on what the session id keeps of it: "abc:1" and "abc/1" would
    // both be import__<harness>__abc-1 and land in one server session.
    const part = sessionIdPart(id);
    if (part === "unknown" && id !== "unknown") return problem('"id" has no letter, digit or ._@- a session id can keep');
    if (seen.has(part)) {
      const first = seen.get(part);
      return problem(first.id === id ? `repeats the id of line ${first.line}` : `id becomes the same session id as line ${first.line} (${part})`);
    }
    seen.set(part, { id, line });
    const messages = [];
    let dropped = 0;
    for (const [i, message] of record.messages.entries()) {
      if (!isPlainObject(message)) return problem(`messages[${i}] is not an object`);
      // Tool calls, system prompts and the like: never sent, only counted.
      if (message.role !== "user" && message.role !== "assistant") {
        dropped += 1;
        continue;
      }
      if (typeof message.text !== "string") return problem(`messages[${i}].text must be a string`);
      const clean = cleanText(message.text);
      if (clean) messages.push({ role: message.role, text: clean, createdAt: parseableTime(message.created_at) });
    }
    droppedMessages += dropped;
    const updated = Date.parse(parseableTime(record.updated_at));
    sessions.push({
      nativeId: id,
      liveId: id,
      cwd,
      messages,
      utility: record.subagent === true,
      updatedAtMs: Number.isFinite(updated) ? updated : 0,
      line,
    });
  });
  if (problems.length) {
    const shown = problems.slice(0, MAX_REPORTED_PROBLEMS).map((p) => `  line ${p.line}: ${p.reason}`);
    if (problems.length > shown.length) shown.push(`  ... and ${problems.length - shown.length} more`);
    throw new InputError(`${path}: ${plural(problems.length, "line")} cannot be read; nothing was sent. Fix the converter and run again:\n${shown.join("\n")}`, problems);
  }
  return { sessions, droppedMessages };
}

// ---------------------------------------------------------------------------
// Planning
// ---------------------------------------------------------------------------

function isWithin(child, parentDir) {
  if (!child || !parentDir) return false;
  const rel = relative(parentDir, child);
  return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel));
}

function canonical(path) {
  try {
    return realpathSync(path);
  } catch {
    return resolve(path);
  }
}

function lastActivity(session, mtimeMs) {
  let latest = 0;
  for (const message of session.messages) {
    const at = Date.parse(message.createdAt);
    if (Number.isFinite(at) && at > latest) latest = at;
  }
  return Math.max(latest, mtimeMs || 0);
}

/** Consecutive messages of one role become one, as a turn: tool-only steps in between were dropped. */
export function mergeTurns(messages) {
  const out = [];
  for (const message of messages) {
    const last = out[out.length - 1];
    if (last && last.role === message.role) last.text = `${last.text}\n\n${message.text}`;
    else out.push({ ...message });
  }
  return out;
}

const sessionIdPart = (value) => String(value || "").trim().replace(/[^a-zA-Z0-9_.@-]+/g, "-").replace(/-{2,}/g, "-").replace(/^[-.]+|[-.]+$/g, "") || "unknown";

export function sessionId(harness, nativeId) {
  return ["import", harness, nativeId].map(sessionIdPart).join("__");
}

export function liveSessionId(harness, liveId) {
  return (HARNESSES[harness]?.liveId || codexLiveId)(liveId);
}

const NO_DERIVED_PEER = Object.freeze({ peerId: "", origin: "not derived" });

/**
 * Read the history and decide, locally, which sessions are in scope. The
 * server is not asked here; `classify` does that.
 */
export function planImport(opts, { env = process.env, now = Date.now(), connection } = {}) {
  const harness = opts.harness;
  const inputPath = opts.input ? expandHome(opts.input, env) : "";
  const root = inputPath || historyRoot(harness, env, opts.historyDir);
  const sinceMs = parseSince(opts.since, now);
  const activeMs = parseAge(opts.skipActive);
  const projectDir = opts.allProjects ? "" : canonical(opts.cwd || process.cwd());
  const forcedPeer = opts.peer === "none" ? { peerId: "", origin: "--peer none" } : (opts.peer ? { peerId: opts.peer, origin: "--peer" } : null);
  const peerCache = new Map();
  const peerFor = (cwd) => {
    if (forcedPeer) return forcedPeer;
    if (!PLUGIN_HARNESS[harness]) return NO_DERIVED_PEER;
    if (!peerCache.has(cwd)) peerCache.set(cwd, resolvePeer({ cwd, harness, connection, env }));
    return peerCache.get(cwd);
  };
  const projectPeer = projectDir ? peerFor(projectDir) : null;
  const secrets = [connection?.apiKey].filter(Boolean);
  // Sessions picked by id are wanted whatever their age or project, and
  // however many there are; only the active-session guard still applies.
  const picked = opts.sessions?.length ? opts.sessions : null;
  const pickedBy = new Map(picked?.map((token) => [token, []]) || []);
  const pickedSkips = [];

  const skipped = { outsideWindow: 0, otherProject: 0, active: 0, empty: 0, utility: 0, overLimit: 0 };
  // An input file is read whole (and refused whole) before anything else.
  const input = inputPath ? readInputSessions(inputPath) : null;
  if (input) skipped.undated = 0;
  const sources = input
    ? input.sessions.map((parsed) => ({ path: `${inputPath}:${parsed.line}`, mtimeMs: 0, parsed }))
    : discoverSessionFiles(harness, root);
  const candidates = [];
  for (const file of sources) {
    // A file last written before the window holds nothing newer.
    if (!picked && sinceMs && file.mtimeMs && file.mtimeMs < sinceMs) {
      skipped.outsideWindow += 1;
      continue;
    }
    const parsed = file.parsed || (harness === "claude_code" ? parseClaudeSession(file.path) : parseCodexSession(file.path));
    const firstUser = parsed.messages.find((message) => message.role === "user");
    const utility = Boolean(parsed.utility || (firstUser && UTILITY_PROMPTS.some((re) => re.test(firstUser.text))));
    if (picked) {
      let hit = false;
      for (const token of picked) {
        const kind = matchesSession(token, harness, parsed.nativeId);
        if (!kind) continue;
        hit = true;
        pickedBy.get(token).push({ id: parsed.nativeId, kind, importable: !utility && parsed.messages.length > 0 });
      }
      if (!hit) continue;
    }
    // A picked session's skip counts only once it is known to be wanted.
    const skip = (reason) => {
      if (picked) pickedSkips.push({ id: parsed.nativeId, reason });
      else skipped[reason] += 1;
    };
    if (utility) {
      skip("utility");
      continue;
    }
    if (!parsed.messages.length) {
      skip("empty");
      continue;
    }
    const last = lastActivity(parsed, file.mtimeMs || parsed.updatedAtMs);
    // No time to hold --since or --skip-active against.
    if (input && !last) {
      skip("undated");
      continue;
    }
    if (!picked && sinceMs && last < sinceMs) {
      skipped.outsideWindow += 1;
      continue;
    }
    const cwd = parsed.cwd ? canonical(parsed.cwd) : "";
    const peer = peerFor(parsed.cwd || "");
    if (projectDir && !picked) {
      // One repository is one project wherever it is checked out; a peer
      // somebody named is the same everywhere and says nothing about that.
      const derived = projectPeer?.peerId && String(projectPeer.origin).includes("{");
      const samePeer = Boolean(!forcedPeer && derived && peer.peerId === projectPeer.peerId);
      if (!samePeer && !isWithin(cwd, projectDir)) {
        skipped.otherProject += 1;
        continue;
      }
    }
    if (activeMs && now - last < activeMs) {
      skip("active");
      continue;
    }
    candidates.push({ ...parsed, path: file.path, lastActivityMs: last, peer });
  }

  let pool = candidates;
  if (picked) {
    // Per id given: exact matches (native or import id) win; a prefix counts
    // only when nothing matches exactly, so a full id that is also the start
    // of another (session-1 / session-10, a Codex parent and its forks) is
    // never ambiguous.
    const wanted = new Set();
    const missing = [];
    const unimportable = [];
    const ambiguous = [];
    for (const [token, hits] of pickedBy) {
      const exact = hits.filter((h) => h.kind === "exact");
      const chosen = exact.length ? exact : hits;
      const ids = new Set(chosen.map((h) => h.id));
      if (!ids.size) missing.push(token);
      else if (ids.size > 1) ambiguous.push(token);
      else if (!chosen.some((h) => h.importable)) unimportable.push(token);
      else wanted.add([...ids][0]);
    }
    const problems = [];
    if (missing.length) problems.push(`no session matches ${missing.map((t) => JSON.stringify(t)).join(", ")}`);
    if (unimportable.length) problems.push(`${unimportable.map((t) => JSON.stringify(t)).join(", ")} matches nothing importable (an empty or plugin-internal session)`);
    if (ambiguous.length) problems.push(`${ambiguous.map((t) => JSON.stringify(t)).join(", ")} matches more than one session; give more of the id`);
    if (problems.length) throw new UsageError(`--session: ${problems.join("; ")}. Nothing was imported; run --list to see the ids`);
    for (const { id, reason } of pickedSkips) if (wanted.has(id)) skipped[reason] += 1;
    pool = candidates.filter((c) => wanted.has(c.nativeId));
  }

  pool.sort((a, b) => b.lastActivityMs - a.lastActivityMs);
  // --list is for choosing, so it is not held to the import default of 20.
  const limit = picked ? pool.length : (opts.list && !opts.maxSessionsGiven ? LIST_LIMIT : opts.maxSessions);
  skipped.overLimit = Math.max(0, pool.length - limit);
  const sessions = pool.slice(0, limit).map((candidate) => {
    const turns = mergeTurns(candidate.messages).map((turn) => {
      let text = redactSecrets(turn.text, secrets);
      if (text.length > opts.maxMessageChars) {
        text = `${text.slice(0, opts.maxMessageChars)}\n... [truncated, ${text.length - opts.maxMessageChars} more characters]`;
      }
      return { role: turn.role, text, createdAt: turn.createdAt };
    });
    const userTurns = turns.filter((turn) => turn.role === "user");
    const firstUser = userTurns[0];
    const lastUser = userTurns.length > 1 ? userTurns[userTurns.length - 1] : null;
    return {
      id: sessionId(harness, candidate.nativeId),
      liveId: liveSessionId(harness, candidate.liveId),
      nativeId: candidate.nativeId,
      path: candidate.path,
      cwd: candidate.cwd,
      peerId: candidate.peer.peerId,
      lastActivity: new Date(candidate.lastActivityMs).toISOString(),
      title: (firstUser?.text || "").replace(/\s+/g, " ").slice(0, 80),
      firstPrompt: preview(firstUser?.text) || null,
      lastPrompt: preview(lastUser?.text) || null,
      chars: turns.reduce((sum, turn) => sum + turn.text.length, 0),
      turns,
    };
  });
  return { harness, root, projectDir, projectPeer, sinceMs, activeMs, sessions, skipped, droppedMessages: input?.droppedMessages, picked, limit };
}

const PREVIEW_CHARS = 160;

function preview(text) {
  const flat = String(text || "").replace(/\s+/g, " ").trim();
  return flat.length > PREVIEW_CHARS ? `${flat.slice(0, PREVIEW_CHARS - 1)}…` : flat;
}

/** How a --session token names a session: "exact" (native or import id), "prefix" (8+ characters), or not at all. */
function matchesSession(token, harness, nativeId) {
  const id = String(nativeId || "");
  if (!id) return "";
  if (token === id || token === sessionId(harness, id)) return "exact";
  return token.length >= 8 && id.startsWith(token) ? "prefix" : "";
}

// ---------------------------------------------------------------------------
// Server
// ---------------------------------------------------------------------------

export function createClient(connection, { fetchImpl = globalThis.fetch, timeoutMs = 30000 } = {}) {
  const headers = { "Content-Type": "application/json", "User-Agent": USER_AGENT };
  if (connection.apiKey) headers.Authorization = `Bearer ${connection.apiKey}`;
  if (connection.sendIdentityHeaders && connection.account) headers["X-OpenViking-Account"] = connection.account;
  if (connection.sendIdentityHeaders && connection.user) headers["X-OpenViking-User"] = connection.user;
  return async function request(method, path, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetchImpl(`${connection.baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      const json = await response.json().catch(() => null);
      const ok = response.ok && json?.status !== "error";
      return { ok, status: response.status, result: json?.result ?? null, error: ok ? null : (json?.error || { message: `HTTP ${response.status}` }) };
    } catch (error) {
      return { ok: false, status: 0, result: null, error: { message: error?.name === "AbortError" ? "timed out" : (error?.message || String(error)) } };
    } finally {
      clearTimeout(timer);
    }
  };
}

const isNotFound = (res) => res.status === 404 || res.error?.code === "NOT_FOUND";

class ServerError extends Error {
  constructor(message, res) {
    super(message);
    this.res = res;
  }
}

function describeFailure(res) {
  if (res.status === 401 || res.status === 403) {
    return `the server rejected the API key (HTTP ${res.status})`;
  }
  if (res.status === 0) return `could not reach the server (${res.error?.message || "network error"})`;
  return `HTTP ${res.status}: ${res.error?.message || res.error?.code || "error"}`;
}

/**
 * What the server already holds for each planned session: `new`, `partial`
 * (created by an earlier run that stopped before its commit), `imported` or
 * `live` (the plugin captured it). Reads only.
 */
export async function classify(request, sessions) {
  for (const session of sessions) {
    const own = await request("GET", `/api/v1/sessions/${encodeURIComponent(session.id)}`);
    if (own.ok) {
      const commits = Number(own.result?.commit_count || 0);
      session.state = commits > 0 ? "imported" : "partial";
      session.sentBefore = commits > 0 ? 0 : Number(own.result?.message_count || 0);
      continue;
    }
    if (!isNotFound(own)) throw new ServerError(describeFailure(own), own);
    const live = await request("GET", `/api/v1/sessions/${encodeURIComponent(session.liveId)}`);
    if (live.ok) {
      session.state = "live";
      continue;
    }
    if (!isNotFound(live)) throw new ServerError(describeFailure(live), live);
    session.state = "new";
    session.sentBefore = 0;
  }
}

function messagePayload(turn, peerId) {
  const payload = { role: turn.role, parts: [{ type: "text", text: turn.text }] };
  if (turn.createdAt) payload.created_at = turn.createdAt;
  if (peerId) payload.peer_id = peerId;
  return payload;
}

/** Create (auto-commit off), send what is missing in batches of 100, commit once. */
export async function importSession(request, session) {
  if (session.state === "new") {
    // Auto-commit off: the one commit below is the only extraction this costs.
    const created = await request("POST", "/api/v1/sessions", { session_id: session.id, auto_commit_policy: null });
    if (!created.ok && created.status !== 409 && created.error?.code !== "ALREADY_EXISTS") {
      throw new ServerError(describeFailure(created), created);
    }
  }
  const pending = session.turns.slice(session.sentBefore || 0).map((turn) => messagePayload(turn, session.peerId));
  let sent = 0;
  for (let start = 0; start < pending.length; start += BATCH_LIMIT) {
    const chunk = pending.slice(start, start + BATCH_LIMIT);
    const res = await request("POST", `/api/v1/sessions/${encodeURIComponent(session.id)}/messages/batch`, { messages: chunk });
    if (!res.ok) throw new ServerError(describeFailure(res), res);
    sent += chunk.length;
  }
  const commit = await request("POST", `/api/v1/sessions/${encodeURIComponent(session.id)}/commit`, { keep_recent_count: 0 });
  if (!commit.ok) throw new ServerError(describeFailure(commit), commit);
  return { sent, taskId: commit.result?.task_id || "" };
}

async function waitForTasks(request, taskIds, { timeoutMs = 120_000, sleep = (ms) => new Promise((r) => setTimeout(r, ms)) } = {}) {
  const deadline = Date.now() + timeoutMs;
  const status = {};
  let open = taskIds.filter(Boolean);
  while (open.length && Date.now() < deadline) {
    const still = [];
    for (const id of open) {
      const res = await request("GET", `/api/v1/tasks/${encodeURIComponent(id)}`);
      const value = res.ok ? String(res.result?.status || "") : "unknown";
      status[id] = value;
      if (["pending", "running", "cancelling"].includes(value)) still.push(id);
    }
    open = still;
    if (open.length) await sleep(2000);
  }
  return status;
}

// ---------------------------------------------------------------------------
// Output
// ---------------------------------------------------------------------------

function shortHome(path, env) {
  const home = String(env.HOME || "").trim() || homedir();
  return path && (path === home || path.startsWith(`${home}${sep}`)) ? `~${path.slice(home.length)}` : path;
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function duration(ms) {
  const minutes = Math.round(ms / 60000);
  return minutes % 60 === 0 ? plural(minutes / 60, "hour") : plural(minutes, "minute");
}

function kb(chars) {
  return chars < 1024 ? `${chars} B` : `${Math.round(chars / 1024)} KB`;
}

function totals(sessions) {
  return sessions.reduce((acc, s) => {
    acc.sessions += 1;
    acc.messages += s.turns.length - (s.sentBefore || 0);
    acc.chars += s.chars;
    return acc;
  }, { sessions: 0, messages: 0, chars: 0 });
}

export function summarize(plan, { mode, connection, results = [], serverChecked = true, serverNote = "", env = process.env }) {
  const toImport = plan.sessions.filter((s) => !serverChecked || s.state === "new" || s.state === "partial");
  const t = totals(toImport);
  return {
    mode,
    harness: plan.harness,
    historyDir: plan.root,
    server: connection.baseUrl,
    credentials: connection.credentialPath ? shortHome(connection.credentialPath, env) : connection.apiKeySource,
    scope: plan.picked ? "picked" : (plan.projectDir ? "project" : "all-projects"),
    project: plan.picked ? null : (plan.projectDir || null),
    picked: plan.picked || undefined,
    projectPeer: plan.projectPeer?.peerId || null,
    window: { since: plan.sinceMs && !plan.picked ? new Date(plan.sinceMs).toISOString() : null, skipActiveMs: plan.activeMs },
    peerNote: PLUGIN_HARNESS[plan.harness] ? undefined : `${HARNESSES[plan.harness].label} names its peers after the agent, not the directory; none is derived (use --peer)`,
    serverChecked,
    serverNote: serverNote || undefined,
    droppedMessages: plan.droppedMessages,
    toImport: { sessions: t.sessions, messages: t.messages, chars: t.chars, estimatedTokens: Math.round(t.chars / 4) },
    skipped: {
      ...plan.skipped,
      alreadyImported: plan.sessions.filter((s) => s.state === "imported").length,
      capturedByPlugin: plan.sessions.filter((s) => s.state === "live").length,
    },
    sessions: plan.sessions.map((s) => ({
      id: s.id,
      nativeId: s.nativeId,
      state: s.state || "unchecked",
      lastActivity: s.lastActivity,
      messages: s.turns.length,
      chars: s.chars,
      peer: s.peerId || null,
      cwd: s.cwd || null,
      firstPrompt: s.firstPrompt,
      lastPrompt: s.lastPrompt,
    })),
    results,
  };
}

function printText(summary, env) {
  const lines = [];
  const label = HARNESSES[summary.harness].label;
  const header = {
    "dry-run": "OpenViking history import: dry run, no conversation content was sent",
    list: "OpenViking history import: session list, no conversation content was sent",
    import: "OpenViking history import",
  }[summary.mode];
  lines.push(header);
  lines.push(`  harness:    ${label} (${shortHome(summary.historyDir, env)})`);
  lines.push(`  server:     ${summary.server} (credentials: ${summary.credentials || "none"})`);
  if (summary.picked) {
    lines.push(`  picked:     ${plural(summary.picked.length, "session id")} (${summary.picked.join(", ")})`);
  } else if (summary.project) {
    const none = summary.peerNote ? "none" : "none: not a repository";
    lines.push(`  project:    ${shortHome(summary.project, env)} (peer ${summary.projectPeer || none})`);
  } else {
    lines.push("  project:    all projects");
  }
  const since = summary.window.since && !summary.picked ? `since ${summary.window.since.slice(0, 10)}` : "any age";
  const active = summary.window.skipActiveMs ? `, skipping sessions active in the last ${duration(summary.window.skipActiveMs)}` : "";
  lines.push(`  window:     ${since}${active}`);
  if (summary.peerNote && !summary.projectPeer) lines.push(`  peer:       ${summary.peerNote}`);
  if (!summary.serverChecked) lines.push(`  note:       ${summary.serverNote}; sessions already on the server are counted as new`);
  const t = summary.toImport;
  const verb = summary.mode === "import" ? "imported" : "to import";
  if (summary.mode === "list") {
    const open = summary.sessions.filter((x) => x.state === "new" || x.state === "partial" || x.state === "unchecked").length;
    lines.push(`  listed:     ${plural(summary.sessions.length, "session")}, ${open} not yet in OpenViking`);
  } else if (summary.mode === "import") {
    const sent = summary.results.filter((r) => r.ok);
    const messages = sent.reduce((n, r) => n + r.sent, 0);
    lines.push(`  ${verb}:   ${plural(sent.length, "session")}, ${plural(messages, "message")}`);
    const failed = summary.results.filter((r) => !r.ok);
    if (failed.length) lines.push(`  failed:     ${failed.length} (${failed[0].error})`);
    const waited = sent.filter((r) => r.extraction);
    if (waited.length) {
      const done = waited.filter((r) => r.extraction === "completed").length;
      lines.push(`  extraction: ${done} of ${waited.length} finished${done < waited.length ? "; the rest continue in the background" : ""}`);
    }
  } else {
    const tokens = t.estimatedTokens < 1000 ? `${t.estimatedTokens}` : `${Math.round(t.estimatedTokens / 1000)}k`;
    lines.push(`  ${verb}:  ${plural(t.sessions, "session")}, ${plural(t.messages, "message")}, ${kb(t.chars)} of text (about ${tokens} tokens)`);
  }
  const s = summary.skipped;
  const skippedParts = [
    [s.alreadyImported, "already imported"],
    [s.capturedByPlugin, "already captured by the plugin"],
    [s.active, "active"],
    [s.overLimit, "over --max-sessions"],
    [s.outsideWindow, "older than the window"],
    [s.otherProject, s.otherProject === 1 ? "other project" : "other projects"],
    [s.empty + s.utility, "empty or plugin-internal"],
    [s.undated, "undated (no time on any message)"],
  ].filter(([n]) => n).map(([n, what]) => `${n} ${what}`);
  if (skippedParts.length) lines.push(`  skipped:    ${skippedParts.join(", ")}`);
  if (summary.droppedMessages) {
    lines.push(`  dropped:    ${plural(summary.droppedMessages, "message")} in the input with a role other than user or assistant`);
  }
  if (summary.mode === "list") {
    if (summary.sessions.length) lines.push("  sessions (newest first; id, last active, messages, state, directory):");
    for (const x of summary.sessions) {
      const state = { new: "new", partial: "partly imported", imported: "already imported", live: "captured by the plugin", unchecked: "not checked" }[x.state] || x.state;
      lines.push(`    ${x.nativeId}  ${x.lastActivity.slice(0, 16).replace("T", " ")}Z  ${x.messages} msgs  ${state}  ${shortHome(x.cwd || "-", env)}`);
      if (x.firstPrompt) lines.push(`      first: ${JSON.stringify(x.firstPrompt)}`);
      if (x.lastPrompt) lines.push(`      last:  ${JSON.stringify(x.lastPrompt)}`);
    }
  }
  const listed = summary.mode === "list" ? [] : summary.sessions.filter((x) => x.state !== "imported" && x.state !== "live");
  if (listed.length) {
    lines.push("  sessions:");
    for (const x of listed) {
      const peer = x.peer ? ` peer ${x.peer}` : "";
      lines.push(`    ${x.lastActivity.slice(0, 16).replace("T", " ")}Z  ${String(x.messages).padStart(4)} msgs${peer}  ${x.firstPrompt ? JSON.stringify(x.firstPrompt.slice(0, 80)) : ""}`.trimEnd());
    }
  }
  if (summary.mode === "list") {
    const scope = [];
    if (summary.skipped.overLimit) scope.push(`${summary.skipped.overLimit} more not listed: raise --max-sessions`);
    if (summary.skipped.outsideWindow) scope.push(`${summary.skipped.outsideWindow} older: widen --since (e.g. --since all)`);
    if (summary.skipped.otherProject) scope.push(`${summary.skipped.otherProject} in other projects: add --all-projects`);
    if (scope.length) lines.push(`Not shown: ${scope.join("; ")}.`);
    lines.push(summary.sessions.length
      ? "To import some of them: --session <id>[,<id>...], as a dry run first, then with --yes."
      : "No sessions in this scope.");
  } else if (summary.mode === "dry-run") {
    lines.push(t.sessions
      ? "Run again with --yes to import. Each session is committed once, which starts memory extraction on the server."
      : "Nothing to import.");
  } else if (!summary.results.length) {
    lines.push("Nothing to import.");
  } else if (summary.results.some((r) => r.ok && r.extraction !== "completed")) {
    lines.push("Memory extraction runs in the background on the server; new memories show up within a few minutes.");
  }
  return `${lines.join("\n")}\n`;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

export async function main(argv = process.argv.slice(2), { env = process.env, stdout = process.stdout, stderr = process.stderr, fetchImpl = globalThis.fetch, now = Date.now() } = {}) {
  let opts;
  try {
    opts = parseArgs(argv);
  } catch (error) {
    stderr.write(`import-history: ${error.message}\n\n${USAGE}`);
    return 2;
  }
  if (opts.help) {
    stdout.write(USAGE);
    return 0;
  }
  const requested = String(opts.harness || "").trim().toLowerCase();
  if (!requested) {
    stderr.write(`import-history: --harness is required\n\n${USAGE}`);
    return 2;
  }
  const harness = Object.hasOwn(HARNESS_ALIASES, requested) ? HARNESS_ALIASES[requested] : undefined;
  if (!harness) {
    stderr.write(`import-history: unknown harness ${JSON.stringify(opts.harness)}; use claude or codex, or with --input one of ${INPUT_NAMES}\n`);
    return 2;
  }
  if (!opts.input && !HARNESSES[harness].native) {
    stdout.write(`History import for ${requested} is not supported yet. Supported: claude (Claude Code), codex.\n`
      + `Its sessions can be imported with --input <file.jsonl>, written by a converter (see --help).\n`);
    return 0;
  }
  if (opts.input && opts.historyDir) {
    stderr.write(`import-history: --input and --history-dir cannot be used together\n\n${USAGE}`);
    return 2;
  }
  opts.harness = harness;
  if (typeof fetchImpl !== "function") {
    stderr.write("import-history: this Node.js has no fetch; Node.js 18 or newer is required\n");
    return 1;
  }

  const connection = resolveConnection(PLUGIN_HARNESS[harness] || harness, { env });
  let plan;
  try {
    plan = planImport(opts, { env, now, connection });
  } catch (error) {
    if (error instanceof UsageError) {
      stderr.write(`import-history: ${error.message}\n`);
      return 2;
    }
    if (error instanceof InputError) {
      stderr.write(`import-history: ${error.message}\n`);
      if (opts.json) stdout.write(`${JSON.stringify({ error: "invalid input", message: error.message, input: opts.input, problems: error.problems }, null, 2)}\n`);
      return 1;
    }
    throw error;
  }
  if (!opts.input && !existsSync(plan.root)) {
    stdout.write(`No ${HARNESSES[harness].label} history at ${shortHome(plan.root, env)}; nothing to import.\n`);
    return 0;
  }

  const request = createClient(connection, { fetchImpl });
  const write = !opts.dryRun && opts.yes;
  let serverChecked = true;
  let serverNote = "";
  try {
    await classify(request, plan.sessions);
  } catch (error) {
    if (!(error instanceof ServerError)) throw error;
    if ([401, 403].includes(error.res?.status)) {
      const where = connection.apiKeySource === "env" ? "OPENVIKING_API_KEY" : (shortHome(connection.credentialPath, env) || "~/.openviking/ovcli.conf");
      error.message += `; check the key in ${where}`;
    }
    if (write) {
      stderr.write(`import-history: ${error.message}. Nothing was imported.\n`);
      return 1;
    }
    serverChecked = false;
    serverNote = `could not check the server: ${error.message}`;
  }

  const results = [];
  if (write) {
    for (const session of plan.sessions) {
      if (session.state !== "new" && session.state !== "partial") continue;
      try {
        const done = await importSession(request, session);
        results.push({ id: session.id, ok: true, ...done });
      } catch (error) {
        if (!(error instanceof ServerError)) throw error;
        results.push({ id: session.id, ok: false, sent: 0, error: error.message });
        // An auth or network failure fails every session after it the same way.
        if ([0, 401, 403].includes(error.res?.status)) break;
      }
    }
    if (opts.wait && results.some((r) => r.taskId)) {
      const status = await waitForTasks(request, results.map((r) => r.taskId));
      for (const r of results) if (r.taskId) r.extraction = status[r.taskId] || "unknown";
    }
  }

  const mode = opts.list ? "list" : (write ? "import" : "dry-run");
  const summary = summarize(plan, { mode, connection, results, serverChecked, serverNote, env });
  stdout.write(opts.json ? `${JSON.stringify(summary, null, 2)}\n` : printText(summary, env));
  return results.some((r) => !r.ok) ? 1 : 0;
}

const invokedDirectly = (() => {
  try {
    return import.meta.url === pathToFileURL(realpathSync(process.argv[1] || "")).href;
  } catch {
    return false;
  }
})();

if (invokedDirectly) {
  main().then((code) => { process.exitCode = code; }, (error) => {
    process.stderr.write(`import-history: ${error?.stack || error}\n`);
    process.exitCode = 1;
  });
}
