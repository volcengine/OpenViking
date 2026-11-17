/**
 * Find one Kimi Code session's directory, and say why when it cannot.
 *
 * The host keeps two records that disagree in useful ways:
 *
 *   ${KIMI_CODE_HOME}/session_index.jsonl   append-only; one line per create,
 *                                          one more per delete
 *   ${KIMI_CODE_HOME}/sessions/<workspace>/<session>/state.json
 *
 * The index is the authority, so its *last* line for a session wins: a session
 * that was deleted and later re-created is live, and one whose last line says
 * `deleted: true` is gone even though an earlier line still names its
 * directory. Answering from the first matching line — what the old adapter did
 * — resurrects deleted sessions and loses re-created ones.
 *
 * Everything else here exists because the alternative is reading the wrong
 * file: the index can be truncated mid-line, paths arrive with either
 * separator and any casing on Windows, `sessionDir` is data that could point
 * outside the sessions root, and `state.json` could belong to another session.
 * Each of those becomes a distinct status, because the caller's response
 * differs: `missing` means skip, `unreadable` means keep cursors and stop,
 * `ambiguous` means refuse to guess.
 */

import { open, readFile, readdir, realpath, stat } from "node:fs/promises";
import { homedir } from "node:os";
import { basename, dirname, isAbsolute, join, normalize, relative, resolve, sep } from "node:path";

/** The tail of the index worth reading. Records are small; this is thousands. */
const INDEX_TAIL_BYTES = 512 * 1024;

/** How many `<workspace>/<session>` directories the fallback scan will consider. */
const FALLBACK_WORKSPACE_LIMIT = 200;

const SESSION_ID_RE = /^[A-Za-z0-9._-]{1,128}$/;

/** Where the host keeps its state. `KIMI_CODE_HOME` is the only override. */
export function kimiCodeHome(env = process.env) {
  const configured = String(env.KIMI_CODE_HOME || "").trim();
  return configured ? resolve(configured) : join(homedir(), ".kimi-code");
}

/** A session id the host could have generated, and safe to use as a path segment. */
export function isValidSessionId(value) {
  const id = String(value || "");
  return SESSION_ID_RE.test(id) && id !== "." && id !== "..";
}

function normalizeForCompare(path, platform = process.platform) {
  const resolved = normalize(path);
  return platform === "win32" ? resolved.toLowerCase() : resolved;
}

function samePath(a, b, platform = process.platform) {
  return normalizeForCompare(a, platform) === normalizeForCompare(b, platform);
}

function isInside(root, candidate, platform = process.platform) {
  const rel = relative(normalizeForCompare(root, platform), normalizeForCompare(candidate, platform));
  return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel));
}

/**
 * Read the last complete line of a file.
 *
 * The index is append-only and can be read while the host is writing to it, so
 * a read can end in a partial record. Only complete lines are returned; a
 * partial tail is dropped rather than guessed at, because a half-written JSON
 * object that happens to parse would be worse than no record.
 */
async function readTail(file, maxBytes) {
  const handle = await open(file, "r");
  try {
    const info = await handle.stat();
    const size = info.size;
    const start = Math.max(0, size - maxBytes);
    const length = size - start;
    if (length <= 0) return { text: "", truncated: false, size };
    const buffer = Buffer.alloc(length);
    // `read` may return fewer bytes than asked for on a pipe or a network
    // filesystem. Losing the tail would drop the newest index record and send
    // the caller into the bounded scan for a session it could have found.
    let read = 0;
    while (read < length) {
      const { bytesRead } = await handle.read(buffer, read, length - read, start + read);
      if (bytesRead <= 0) break;
      read += bytesRead;
    }
    if (read < length) {
      return { text: "", truncated: start > 0, size };
    }
    let text = buffer.toString("utf8");
    // A window that starts mid-record begins with a fragment; drop it.
    if (start > 0) {
      const newline = text.indexOf("\n");
      text = newline === -1 ? "" : text.slice(newline + 1);
    }
    // And a window that ends mid-record ends with a fragment.
    if (!text.endsWith("\n")) {
      const newline = text.lastIndexOf("\n");
      text = newline === -1 ? "" : text.slice(0, newline + 1);
    }
    return { text, truncated: start > 0, size };
  } finally {
    await handle.close();
  }
}

/**
 * The last index record for `sessionId`.
 *
 * Returns `{ found: true, record }` when the newest line names the session,
 * `{ found: false }` when the index has no line for it, and `{ error }` when
 * the index could not be read. A deleted record is returned as found — the
 * caller decides that the session is gone, rather than this function hiding
 * the deletion behind "not in the index".
 */
export async function findLatestIndexRecord(home, sessionId, { platform = process.platform } = {}) {
  const file = join(home, "session_index.jsonl");
  let tail;
  try {
    tail = await readTail(file, INDEX_TAIL_BYTES);
  } catch (error) {
    if (error?.code === "ENOENT") return { found: false, indexMissing: true };
    return { error: error?.code || "index_unreadable", indexMissing: false };
  }

  const lines = tail.text.split("\n");
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    const line = lines[index].trim();
    if (!line) continue;
    let row;
    try {
      row = JSON.parse(line);
    } catch {
      // A single damaged line is not a reason to lose the rest of the tail.
      continue;
    }
    if (!row || typeof row !== "object") continue;
    if (String(row.sessionId ?? row.session_id ?? "") !== sessionId) continue;
    return { found: true, record: row, indexTruncated: tail.truncated };
  }
  return { found: false, indexMissing: false, indexTruncated: tail.truncated };
}

/**
 * Validate a session directory and read its `state.json`.
 *
 * `sessionDir` comes from the index, so it is untrusted input that decides
 * which file is read next. Two checks stand between it and an arbitrary local
 * file: the realpath of the directory must stay inside the realpath of the
 * sessions root (so a symlink cannot escape), and the `id` inside `state.json`
 * must be the session that was asked for (so a copied or stale directory is
 * not mistaken for this one).
 */
async function inspectSessionDir(sessionDir, sessionId, sessionsRoot, { platform = process.platform } = {}) {
  let realDir;
  let realRoot;
  try {
    realDir = await realpath(sessionDir);
  } catch (error) {
    if (error?.code === "ENOENT") return { status: "missing" };
    return { status: "unreadable", error: error?.code || "session_dir_unreadable" };
  }
  try {
    realRoot = await realpath(sessionsRoot);
  } catch (error) {
    if (error?.code === "ENOENT") return { status: "missing" };
    return { status: "unreadable", error: error?.code || "sessions_root_unreadable" };
  }
  if (!isInside(realRoot, realDir, platform)) {
    return { status: "unreadable", error: "session_dir_outside_root" };
  }

  const statePath = join(realDir, "state.json");
  let raw;
  try {
    raw = await readFile(statePath, "utf8");
  } catch (error) {
    if (error?.code === "ENOENT") return { status: "missing", sessionDir: realDir };
    return { status: "unreadable", error: error?.code || "state_unreadable", sessionDir: realDir };
  }

  let state;
  try {
    state = JSON.parse(raw);
  } catch {
    return { status: "unreadable", error: "state_invalid_json", sessionDir: realDir };
  }
  if (!state || typeof state !== "object" || Array.isArray(state)) {
    return { status: "unreadable", error: "state_not_an_object", sessionDir: realDir };
  }
  if (state.id !== undefined && String(state.id) !== sessionId) {
    // The directory answered for a different session: refuse rather than
    // capture someone else's transcript into this one's memory.
    return { status: "unreadable", error: "state_session_mismatch", sessionDir: realDir };
  }
  return { status: "ok", sessionDir: realDir, state };
}

/**
 * Bounded scan for `<workspace>/<sessionId>` when the index cannot answer.
 *
 * Only used when the index is absent or unreadable. The scan is bounded in
 * workspaces so a machine with hundreds of projects cannot turn one hook into
 * a full directory walk, and it reports `ambiguous` rather than picking a
 * winner when two workspaces both hold the id.
 */
async function scanForSessionDir(sessionsRoot, sessionId, { platform = process.platform } = {}) {
  let workspaces;
  try {
    workspaces = await readdir(sessionsRoot, { withFileTypes: true });
  } catch (error) {
    if (error?.code === "ENOENT") return { status: "missing" };
    return { status: "unreadable", error: error?.code || "sessions_unreadable" };
  }

  const candidates = [];
  let scanned = 0;
  let truncated = false;
  for (const entry of workspaces) {
    if (!entry.isDirectory()) continue;
    if (scanned >= FALLBACK_WORKSPACE_LIMIT) {
      truncated = true;
      break;
    }
    scanned += 1;
    const candidate = join(sessionsRoot, entry.name, sessionId);
    try {
      if (!(await stat(candidate)).isDirectory()) continue;
    } catch {
      continue;
    }
    candidates.push(candidate);
  }

  if (candidates.length === 0) return { status: "missing", fallbackScanned: scanned, truncated };
  const inspected = [];
  for (const candidate of candidates) {
    const result = await inspectSessionDir(candidate, sessionId, sessionsRoot, { platform });
    if (result.status === "ok") inspected.push(result);
  }
  if (inspected.length === 1) return { ...inspected[0], fallbackScanned: scanned, truncated };
  if (inspected.length > 1) {
    return { status: "ambiguous", candidates: inspected.map((item) => item.sessionDir), fallbackScanned: scanned };
  }
  return { status: "missing", fallbackScanned: scanned, truncated };
}

/**
 * The agents of one session, in the order the host created them.
 *
 * Agent ids are not `agent-N`: the host names the main agent `main` and every
 * subagent whatever its profile was called. So the enumeration comes from
 * `state.json`'s `agents` object, and the directory listing is only a
 * cross-check. An agent whose homedir resolves outside the session's own
 * `agents/` tree is dropped: that metadata decides which file is read, and it
 * must not be able to point at an arbitrary local transcript.
 */
export async function listSessionAgents(sessionDir, state) {
  const agentsRoot = join(sessionDir, "agents");
  let realAgentsRoot;
  try {
    realAgentsRoot = await realpath(agentsRoot);
  } catch (error) {
    if (error?.code === "ENOENT") return { agents: [], error: undefined };
    return { agents: [], error: error?.code || "agents_unreadable" };
  }

  let onDisk = [];
  try {
    onDisk = (await readdir(realAgentsRoot, { withFileTypes: true }))
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name);
  } catch {
    onDisk = [];
  }

  const declared = state?.agents && typeof state.agents === "object" && !Array.isArray(state.agents)
    ? Object.keys(state.agents)
    : [];
  // Declared order is creation order; anything on disk but not declared is
  // still a real agent directory the host wrote, so it is appended.
  const ids = [...declared, ...onDisk.filter((id) => !declared.includes(id))];

  const agents = [];
  for (const id of ids) {
    const meta = declared.includes(id) ? state.agents[id] : undefined;
    if (meta !== undefined && (meta === null || typeof meta !== "object" || Array.isArray(meta))) continue;

    const defaultHome = join(realAgentsRoot, id);
    let home = defaultHome;
    let outside = false;
    if (typeof meta?.homedir === "string" && meta.homedir.trim()) {
      const declaredHome = isAbsolute(meta.homedir) ? meta.homedir : resolve(sessionDir, meta.homedir);
      try {
        const realHome = await realpath(declaredHome);
        if (!isInside(realAgentsRoot, realHome)) outside = true;
        else home = realHome;
      } catch {
        // A declared homedir that does not exist yet is the host's own
        // mid-write state; the default location is where the file will appear.
        home = defaultHome;
      }
    }
    if (outside) {
      agents.push({
        id,
        type: meta?.type,
        homedir: defaultHome,
        labels: meta?.labels,
        error: "homedir_outside_session",
      });
      continue;
    }
    agents.push({
      id,
      type: typeof meta?.type === "string" ? meta.type : undefined,
      homedir: home,
      labels: meta?.labels && typeof meta.labels === "object" ? meta.labels : undefined,
    });
  }

  return { agents, agentsRoot: realAgentsRoot };
}

/** The parent of a subagent, from `labels.parentAgentId` or the legacy top-level field. */
export function agentParentId(agent, state) {
  const fromLabels = agent?.labels?.parentAgentId;
  if (typeof fromLabels === "string" && fromLabels) return fromLabels;
  const fromMeta = state?.agents?.[agent?.id]?.parentAgentId;
  if (typeof fromMeta === "string" && fromMeta) return fromMeta;
  return undefined;
}

/** The profile name of a subagent, from `labels.profileName`. */
export function agentProfileName(agent) {
  const value = agent?.labels?.profileName;
  return typeof value === "string" && value ? value : undefined;
}

/** `forkedFrom` on the session, if the host recorded one. */
export function sessionForkedFrom(state) {
  const value = state?.forkedFrom ?? state?.forked_from;
  if (typeof value === "string" && value) return value;
  if (value && typeof value === "object" && typeof value.id === "string") return value.id;
  return undefined;
}

/** The environment a session runs in. Absent means local, which is the host's own default. */
export function sessionEnvironmentId(state) {
  const value = state?.environmentId ?? state?.environment_id;
  return typeof value === "string" && value ? value : "local";
}

/**
 * Locate one session.
 *
 * Statuses:
 *   ok          `sessionDir` and parsed `state` are usable
 *   missing     no live session with this id (never created, or deleted)
 *   unreadable  it exists but cannot be trusted: I/O error, symlink escape,
 *               malformed state, or state for a different id
 *   ambiguous   more than one directory answers for this id
 *
 * `deleted` is reported separately from `missing` so doctor can tell "this
 * session was removed" from "this id was never here".
 */
export async function locateSession(sessionId, {
  home = kimiCodeHome(),
  platform = process.platform,
} = {}) {
  if (!isValidSessionId(sessionId)) {
    return { status: "missing", reason: "invalid_session_id", home };
  }
  const sessionsRoot = join(home, "sessions");

  const indexed = await findLatestIndexRecord(home, sessionId, { platform });
  if (indexed.error) {
    const scanned = await scanForSessionDir(sessionsRoot, sessionId, { platform });
    return { ...scanned, indexError: indexed.error, home };
  }

  if (indexed.found) {
    const record = indexed.record;
    if (record.deleted === true) {
      return { status: "missing", reason: "deleted", home, record };
    }
    const sessionDir = record.sessionDir ?? record.session_dir;
    if (typeof sessionDir !== "string" || !sessionDir.trim()) {
      // A live record without a directory cannot be resolved, and scanning
      // would be guessing at what the index failed to say.
      return { status: "unreadable", error: "index_record_without_dir", home, record };
    }
    const inspected = await inspectSessionDir(resolve(sessionDir), sessionId, sessionsRoot, { platform });
    return {
      ...inspected,
      home,
      record,
      workDir: typeof record.workDir === "string" ? record.workDir : undefined,
    };
  }

  // The index is present but has no line for this session: either it is older
  // than the session (a write the host has not flushed yet) or the index was
  // rebuilt. A bounded scan is the only remaining evidence.
  const scanned = await scanForSessionDir(sessionsRoot, sessionId, { platform });
  return { ...scanned, home, indexMissing: indexed.indexMissing };
}

/** Does `candidate` still resolve inside `root`? Exposed for the tests and doctor. */
export function isPathInside(root, candidate, platform = process.platform) {
  return isInside(root, candidate, platform);
}

/** The last path segment, for diagnostics that must not print a whole home path. */
export function tailSegment(path) {
  return basename(normalize(String(path || "")));
}

/** The parent directory name, for the same reason. */
export function parentSegment(path) {
  return basename(dirname(normalize(String(path || ""))));
}

export { sep as pathSeparator };
