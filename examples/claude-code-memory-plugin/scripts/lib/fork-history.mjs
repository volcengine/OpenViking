/**
 * Inherited-history detection for forked Claude Code sessions.
 *
 * A forked session — a Claude Desktop side chat (`Cmd+;` / `/btw`), `/fork`,
 * or `claude --resume --fork-session` — gets a fresh `session_id` and a
 * transcript whose leading entries are a verbatim copy of the parent's
 * history. The capture hook keys its cursor on `session_id`, so without help
 * the first Stop in the fork re-captures the whole copied parent history
 * (issue #6524).
 *
 * Two independent, conservative signals locate where the copied prefix ends;
 * both only ever look at a leading run of entries, so an ordinary session —
 * whose entries carry its own session id and uuids no other session recorded —
 * detects nothing and keeps the historical behavior:
 *
 * 1. Foreign session stamp. Copied entries keep the `sessionId` field they
 *    were written with, so a leading run stamped with one id that differs
 *    from the hook's `session_id` is inherited history.
 * 2. Known-uuid prefix. Message uuids survive the copy, and every session
 *    records the uuid hashes of the transcript entries its cursor has passed
 *    (see `recordSessionUuidHashes`). A leading run of uuids another session
 *    already recorded is inherited history. The index deliberately excludes
 *    the current session's own entry: after a rewind to zero the fork must
 *    re-capture its own turns, only the parent's prefix stays skipped.
 *
 * The index keeps sha-256 prefixes (16 hex chars) rather than raw uuids, one
 * file per session under the shared state dir, capped and age-limited: it is
 * a best-effort detection aid, not a record, and a lost entry at worst makes
 * one fork re-capture what its parent captured.
 */

import { createHash } from "node:crypto";
import { mkdir, readdir, readFile, rename, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { STATE_DIR, statePath } from "./state.mjs";

const UUID_DIR_NAME = "captured-uuids";
const UUID_HASH_CHARS = 16;
const MAX_UUIDS_PER_SESSION = 4000;
const INDEX_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;

export function hashUuid(uuid) {
  return createHash("sha256").update(String(uuid), "utf8").digest("hex").slice(0, UUID_HASH_CHARS);
}

function isEntry(value) {
  return Boolean(value) && typeof value === "object";
}

/**
 * Length of the leading run of entries stamped with one foreign session id.
 *
 * Entries without a `sessionId` field (bookkeeping lines copied alongside the
 * history) cannot contradict the run and are walked through; the first entry
 * carrying the current session id — or a different foreign id — ends it.
 * The length is the index after the last foreign-stamped entry, so an
 * unstamped line between stamped ones stays inside the skipped prefix.
 */
export function countForeignSessionPrefix(messages, currentSessionId) {
  let foreignId = null;
  let lastForeign = -1;
  const messages_ = messages || [];
  for (let i = 0; i < messages_.length; i++) {
    const message = messages_[i];
    if (!isEntry(message)) break;
    const stamped = message.sessionId;
    if (typeof stamped !== "string" || !stamped) continue;
    if (stamped === currentSessionId) break;
    if (foreignId === null) foreignId = stamped;
    else if (stamped !== foreignId) break;
    lastForeign = i;
  }
  return lastForeign + 1;
}

/**
 * Length of the leading run of entries whose uuid hashes are all in
 * `knownHashes`. Entries without a uuid are walked through; the first uuid
 * the set does not know ends the run.
 */
export function countKnownUuidPrefix(messages, knownHashes) {
  let lastKnown = -1;
  const messages_ = messages || [];
  for (let i = 0; i < messages_.length; i++) {
    const message = messages_[i];
    if (!isEntry(message)) break;
    const uuid = message.uuid;
    if (typeof uuid !== "string" || !uuid) continue;
    if (!knownHashes.has(hashUuid(uuid))) break;
    lastKnown = i;
  }
  return lastKnown + 1;
}

function uuidFilePath(sessionId) {
  const safe = String(sessionId).replace(/[^a-zA-Z0-9_-]/g, "_");
  return statePath(join(UUID_DIR_NAME, `${safe}.json`));
}

/** The uuid hashes this session's cursor has passed, or an empty list. */
export async function readSessionUuidHashes(sessionId) {
  let raw;
  try {
    raw = await readFile(uuidFilePath(sessionId), "utf-8");
  } catch {
    return [];
  }
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed?.uuids) ? parsed.uuids.filter((h) => typeof h === "string" && h) : [];
  } catch {
    return [];
  }
}

/**
 * Record the uuid hashes of the entries a session's cursor has passed.
 *
 * One file per session, written atomically (temp + rename), so concurrent
 * Stop hooks of different sessions never clobber each other; the file holds
 * only the most recent MAX_UUIDS_PER_SESSION hashes — detection walks from
 * the transcript start, so a fork from a parent whose captured tail exceeds
 * the cap may still re-capture its oldest turns.
 */
export async function recordSessionUuidHashes(sessionId, uuidHashes, now = Date.now()) {
  if (!Array.isArray(uuidHashes) || uuidHashes.length === 0) return;
  const target = uuidFilePath(sessionId);
  try {
    await mkdir(join(STATE_DIR, UUID_DIR_NAME), { recursive: true });
    const tmp = `${target}.${process.pid}.tmp`;
    await writeFile(tmp, JSON.stringify({
      version: 1,
      ts: now,
      uuids: uuidHashes.slice(-MAX_UUIDS_PER_SESSION),
    }));
    await rename(tmp, target);
  } catch { /* best effort: detection degrades to the foreign-stamp signal */ }
}

/**
 * Union of the uuid hashes recorded by every other session, ignoring entries
 * past the age limit. Excluding the current session's own file is what lets a
 * fork re-capture its own turns after its cursor was rewound to zero.
 */
export async function readKnownUuidHashes(excludeSessionId) {
  const known = new Set();
  const dir = statePath(UUID_DIR_NAME);
  const excludeName = `${String(excludeSessionId).replace(/[^a-zA-Z0-9_-]/g, "_")}.json`;
  let files;
  try {
    files = await readdir(dir);
  } catch {
    return known;
  }
  for (const name of files) {
    if (!name.endsWith(".json") || name === excludeName) continue;
    let parsed;
    try {
      parsed = JSON.parse(await readFile(join(dir, name), "utf-8"));
    } catch {
      continue;
    }
    if (!Array.isArray(parsed?.uuids)) continue;
    if (typeof parsed?.ts === "number" && Date.now() - parsed.ts > INDEX_MAX_AGE_MS) continue;
    for (const hash of parsed.uuids) {
      if (typeof hash !== "string" || !hash) continue;
      if (!known.has(hash)) known.add(hash);
    }
  }
  return known;
}
