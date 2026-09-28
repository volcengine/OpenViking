// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
/**
 * Workspace identity: where the workspace root is, and what git calls it.
 *
 * Every hook is a fresh Node process and this runs on prompt-level paths, so
 * the derivation is pure filesystem work — no `git` subprocess. That keeps it
 * inside the tightest hook budget (Codex allows SessionEnd 3s against
 * SessionStart's 70s), and it keeps working where git is absent from PATH or
 * would refuse the repo over dubious ownership.
 *
 * The result is cached per cwd under the state dir so the several hooks of one
 * turn pay for the walk once.
 */

import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, realpathSync, renameSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, isAbsolute, join, parse, resolve, sep } from "node:path";

// The workspace config file names live here, and `workspace-config.mjs`
// re-exports them: the walk below needs the filenames to recognise a directory
// someone marked as a workspace, and it must not pull in the config layer to
// learn them.
export const CONFIG_DIR_NAME = ".openviking";
export const TEAM_FILE = "config.json";
export const LOCAL_FILE = "config.local.json";

const IDENTITY_CACHE_TTL_MS = 60_000;
// 255 is the AGFS path-segment limit; stopping well short leaves room for the
// hash suffix and for anything that later prefixes a peer id.
const MAX_PEER_ID_LENGTH = 100;

export function stateDir(env = process.env) {
  const explicit = String(env.OPENVIKING_STATE_DIR || "").trim();
  if (explicit) return explicit;
  const home = String(env.OPENVIKING_HOME || "").trim();
  const base = home ? home.replace(/^~(?=$|\/)/, homedir()) : join(homedir(), ".openviking");
  return join(base, "state");
}

function shortHash(value) {
  return createHash("sha256").update(String(value)).digest("hex").slice(0, 12);
}

/**
 * Legacy peer sanitation: one byte in, one byte out, no collapsing and no
 * trimming, so a leading `/` still becomes a leading `-`. Kept byte-exact
 * because `peer.source: "cwd"` and the legacy id that dual-read recomputes both
 * depend on it — do not "improve" it.
 */
export function legacySanitize(value) {
  return String(value || "").replace(/[^A-Za-z0-9]/g, "-");
}

/**
 * Readable sanitation for values that were never path-shaped — a normalized
 * remote, a directory name. Mirrors the server's own `_sanitize_component`
 * (`openviking/ingest/peer.py:32`) so both languages agree on the id, then
 * enforces what `validate_identifier_part` additionally requires.
 */
export function sanitizePeerId(value) {
  const raw = String(value || "").trim();
  let cleaned = raw.replace(/[^a-zA-Z0-9_.@-]+/g, "-").replace(/-{2,}/g, "-").replace(/^[-.]+|[-.]+$/g, "");
  if (!cleaned) return "";
  // The server accepts at most one `@` in an identifier part.
  const at = cleaned.indexOf("@");
  if (at !== -1) cleaned = cleaned.slice(0, at + 1) + cleaned.slice(at + 1).replace(/@/g, "-");
  // `ext-` is the server's namespace for base64-encoded external identities,
  // and `__self` its operation-target sentinel. Neither is ours to occupy.
  if (cleaned.startsWith("ext-")) cleaned = `x-${cleaned}`;
  if (cleaned === "__self") cleaned = "self";
  if (cleaned === "." || cleaned === "..") return "";
  if (cleaned.length > MAX_PEER_ID_LENGTH) {
    cleaned = `${cleaned.slice(0, MAX_PEER_ID_LENGTH - 13).replace(/[-.]+$/, "")}-${shortHash(raw)}`;
  }
  return cleaned;
}

/**
 * Normalize a remote URL to `host/path`, lowercased.
 *
 * Both spellings of the same repo converge, and userinfo is dropped — so a
 * remote with an embedded token cannot leak it into a peer id. The cost of
 * case folding is that two repos differing only in case share one namespace on
 * the rare case-sensitive forge.
 */
export function normalizeGitRemote(url) {
  const raw = String(url || "").trim();
  if (!raw) return "";

  // `C:\src\repo` and `C:/src/repo` are one machine's directory, not a shared
  // identity — and the scp pattern would happily read the drive as a host.
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
    // A local clone has no stable shared identity — fall through to the path
    // rules instead of minting one from `file://` or a bare directory.
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

/**
 * Read `[remote "origin"] url` out of a git config with a minimal INI parse.
 *
 * `include` / `includeIf` are deliberately not followed: resolving them means
 * more filesystem walking for a value the fallback chain already covers, so an
 * unreadable remote just falls through to the next template.
 */
export function readGitRemoteUrl(commonDir, remote = "origin") {
  const text = readFileOrEmpty(join(commonDir, "config"));
  if (!text) return "";

  let inSection = false;
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith(";")) continue;
    const section = /^\[([^\]]+)\]/.exec(trimmed);
    if (section) {
      const header = section[1].trim();
      // git folds section and key names to lower case but keeps a quoted
      // subsection exact, so `[Remote "origin"]` is the same section and
      // `[remote "Origin"]` is not.
      const quoted = /^remote\s+"(.*)"$/i.exec(header);
      inSection = Boolean(quoted && quoted[1] === remote);
      continue;
    }
    if (!inSection) continue;
    const pair = /^url\s*=\s*(.*)$/i.exec(trimmed);
    // git treats an unquoted `#` or `;` as starting a comment anywhere on the
    // line, so a trailing note is not part of the URL.
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

  // `commondir` is the worktree signal, so it is read first — a repository
  // that merely lives under a directory called `modules` is not a submodule.
  const commonRef = readFileOrEmpty(join(gitDir, "commondir")).trim();
  const commonDir = commonRef
    ? (isAbsolute(commonRef) ? commonRef : resolve(gitDir, commonRef))
    : gitDir;

  // A submodule keeps its own remote under the superproject's
  // `.git/modules/<name>`; converging it onto the superproject would merge two
  // repositories that release, and are reviewed, separately. Only the segment
  // right after a `.git` directory means that, which is also why a worktree of
  // a submodule resolves here through its own commondir.
  const kind = /[/\\]\.git[/\\]modules[/\\]/.test(`${commonDir}${sep}`)
    ? "submodule"
    : (commonRef ? "worktree" : "repo");
  return { gitDir, commonDir, kind };
}

/** A directory the user made a workspace on purpose, by giving it a config file. */
function hasWorkspaceMarker(dir) {
  for (const name of [TEAM_FILE, LOCAL_FILE]) {
    try {
      if (statSync(join(dir, CONFIG_DIR_NAME, name)).isFile()) return true;
    } catch { /* not marked here */ }
  }
  return false;
}

const NO_ROOT = Object.freeze({ root: "", rootKind: "", git: null, gitRoot: "" });

/**
 * Walk up from `cwd` to the nearest workspace root.
 *
 * A workspace is a git repository, or a directory someone marked as one by
 * creating `.openviking/config.json` (or `config.local.json`) in it. Nearest
 * wins. Above a marked directory the walk continues to the enclosing
 * repository, if any, so the git-shaped variables still resolve there.
 * Anything else — a scratch folder, a download, an app's per-task directory —
 * is not a workspace and gets no peer of its own.
 *
 * `$HOME` and the filesystem root are never workspace roots: a stray `.git`
 * in either would silently make every unrelated directory one workspace.
 */
export function findWorkspaceRoot(cwd, env = process.env) {
  const start = String(cwd || "").trim();
  if (!start) return NO_ROOT;

  let absolute;
  try {
    // A relative `start` makes `resolve` read process.cwd(), which throws when
    // the directory is gone — the very case the catch below is meant to cover.
    absolute = resolve(start);
  } catch {
    return NO_ROOT;
  }

  // `current` and `stopAt` are compared as strings, so they must be
  // canonicalized together. A `$HOME` reached through a symlink plus an
  // unresolvable cwd would otherwise walk straight past the guard.
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
    const parent = dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return root ? { root, rootKind, git: null, gitRoot: "" } : NO_ROOT;
}

function cachePath(cwd, env) {
  return join(stateDir(env), `ws-identity-${shortHash(cwd)}.json`);
}

function readCache(path, now) {
  try {
    const cached = JSON.parse(readFileSync(path, "utf-8"));
    // Bounded on both sides: an entry stamped in the future — a clock that ran
    // fast, or `Infinity` — would otherwise pin a stale identity indefinitely.
    if (typeof cached?.ts !== "number" || cached.ts > now || now - cached.ts > IDENTITY_CACHE_TTL_MS) return null;
    const identity = cached.identity;
    if (identity && typeof identity === "object" && identity.vars && typeof identity.vars === "object") {
      return identity;
    }
  } catch { /* a cold or corrupt cache just costs one walk */ }
  return null;
}

function writeCache(path, identity, now) {
  try {
    mkdirSync(dirname(path), { recursive: true });
    const tmp = `${path}.${process.pid}.tmp`;
    writeFileSync(tmp, JSON.stringify({ ts: now, identity }), { mode: 0o600 });
    renameSync(tmp, path);
  } catch { /* best effort */ }
}

/**
 * Everything the peer templates can substitute, for one cwd.
 *
 * `git_remote` and `dir` are already sanitized; `git_root` and `cwd` carry the
 * legacy byte-for-byte rule, because they are the two that must reproduce a
 * peer minted before any of this existed.
 */
export function resolveWorkspaceIdentity({ cwd = "", env = process.env, cache = true, now = Date.now() } = {}) {
  const key = String(cwd || "");
  const path = cachePath(key, env);
  if (cache) {
    const hit = readCache(path, now);
    if (hit) return hit;
  }

  const { root, rootKind, git, gitRoot } = findWorkspaceRoot(key, env);
  // Only the normalized form is kept. The raw URL may carry a token, and this
  // file outlives the process — writing it here would undo the care
  // `normalizeGitRemote` takes to drop userinfo.
  const remote = git ? normalizeGitRemote(readGitRemoteUrl(git.commonDir)) : "";
  const identity = {
    cwd: key,
    root,
    rootKind,
    isGit: Boolean(git),
    gitKind: git?.kind || "",
    gitCommonDir: git?.commonDir || "",
    gitRoot,
    remote,
    vars: {
      git_remote: sanitizePeerId(remote),
      // The enclosing repository's root, which is not the workspace root when a
      // marker file below it won. Empty outside a repository, so the `git`
      // preset resolves to nothing there rather than to a bare path.
      git_root: git ? legacySanitize(gitRoot) : "",
      cwd: legacySanitize(key),
      dir: root ? sanitizePeerId(root.split(/[/\\]/).filter(Boolean).pop() || "") : "",
    },
  };
  if (cache) writeCache(path, identity, now);
  return identity;
}
