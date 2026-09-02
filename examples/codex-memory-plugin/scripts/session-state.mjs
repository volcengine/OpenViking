/**
 * Per-codex-session state for the OpenViking memory plugin.
 *
 * One state file per codex session_id, holding the long-lived OpenViking
 * session id that we incrementally append turns to via the Stop hook. The
 * OV session id is derived as `cx-<codex-session-id>` for new captures.
 * The OV session is committed (which extracts memories) by SessionEnd, by
 * PreCompact, or by the fallback sweep at SessionStart.
 *
 * Two sidecars live next to `<safeId>.json`:
 *   - `<safeId>.ended.<ts>` — written by the SessionEnd parent hook, lock-free,
 *     so the sweep can still commit the session if the worker never ran. A
 *     whole-object saveState from a concurrent worker cannot clobber it. The
 *     timestamp is in the name so a conditional removal always targets an
 *     immutable path and can never delete a marker written by a later exit.
 *   - `<safeId>.lock`  — an exclusive mkdir lock serializing the writers
 *     (Stop worker, PreCompact, SessionEnd worker, SessionStart sweep) that
 *     all persist the whole state object. A stale lock is taken over in place,
 *     by claiming the `owner` file inside it.
 *
 * State directory: $OPENVIKING_CODEX_STATE_DIR or ~/.openviking/codex-plugin-state
 */

import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, readdir, rename, rm, rmdir, stat, utimes, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { deriveCodexSessionId } from "./shared/session-model.mjs";

const DEFAULT_STATE_DIR = join(homedir(), ".openviking", "codex-plugin-state");

export function getStateDir() {
  return process.env.OPENVIKING_CODEX_STATE_DIR || DEFAULT_STATE_DIR;
}

function safeId(codexSessionId) {
  return String(codexSessionId).replace(/[^a-zA-Z0-9_-]/g, "_");
}

export function deriveOvSessionId(codexSessionId) {
  return deriveCodexSessionId(codexSessionId);
}

export function resolveOvSessionId(state) {
  // Always derive the deterministic cx-* id. Legacy persisted UUIDs from
  // before the cx-* scheme are no longer preserved: the migration window
  // has closed and keeping them would desync recall (which derives cx-*)
  // from capture (which used to echo back the legacy value).
  state.ovSessionId = deriveOvSessionId(state.codexSessionId);
  return state.ovSessionId;
}

function statePath(codexSessionId) {
  return join(getStateDir(), `${safeId(codexSessionId)}.json`);
}

function endedPath(codexSessionId, ts) {
  return join(getStateDir(), `${safeId(codexSessionId)}.ended.${ts}`);
}

// Pre-0.8.1 marker: a bare `<safeId>.ended` whose content is the timestamp.
function legacyEndedPath(codexSessionId) {
  return join(getStateDir(), `${safeId(codexSessionId)}.ended`);
}

/**
 * Marker files for one session, newest first, from an optional pre-read dir
 * listing (`listStates()` already has one).
 */
function endedMarkersFrom(files, codexSessionId) {
  const prefix = `${safeId(codexSessionId)}.ended.`;
  const out = [];
  for (const file of files) {
    if (!file.startsWith(prefix)) continue;
    const ts = Number(file.slice(prefix.length));
    if (Number.isFinite(ts) && ts > 0) out.push({ file, ts });
  }
  return out.sort((a, b) => b.ts - a.ts);
}

async function endedMarkers(codexSessionId) {
  try {
    return endedMarkersFrom(await readdir(getStateDir()), codexSessionId);
  } catch {
    return [];
  }
}

/** Newest marker timestamp for one session, from a pre-read dir listing. */
async function endedAtFrom(files, codexSessionId) {
  const stamps = endedMarkersFrom(files, codexSessionId).map((m) => m.ts);
  if (files.includes(`${safeId(codexSessionId)}.ended`)) {
    const legacy = await legacyEndedAt(codexSessionId);
    if (legacy !== undefined) stamps.push(legacy);
  }
  return stamps.length ? Math.max(...stamps) : undefined;
}

async function legacyEndedAt(codexSessionId) {
  try {
    const raw = await readFile(legacyEndedPath(codexSessionId), "utf-8");
    const ts = Number(raw.trim());
    return Number.isFinite(ts) && ts > 0 ? ts : Date.now();
  } catch {
    return undefined;
  }
}

function lockPath(codexSessionId) {
  return join(getStateDir(), `${safeId(codexSessionId)}.lock`);
}

function defaultState(codexSessionId) {
  const now = Date.now();
  return {
    codexSessionId,
    ovSessionId: null,
    workspacePeerId: "",
    // Last rollout path seen by a capture hook, so the SessionStart sweep can
    // catch up turns for a session whose own workers never ran.
    transcriptPath: null,
    capturedTurnCount: 0,
    createdAt: now,
    lastUpdatedAt: now,
  };
}

export async function loadState(codexSessionId) {
  try {
    const raw = await readFile(statePath(codexSessionId), "utf-8");
    const parsed = JSON.parse(raw);
    return { ...defaultState(codexSessionId), ...parsed };
  } catch {
    return defaultState(codexSessionId);
  }
}

/**
 * Persist state. `touch: false` keeps the existing `lastUpdatedAt` so a write
 * that isn't transcript activity (e.g. releasing `ovSessionId` after a commit)
 * doesn't make a dead session look freshly used to the idle-TTL sweep or to
 * the doctor's orphan count.
 */
export async function saveState(state, { touch = true } = {}) {
  if (!state || !state.codexSessionId) return;
  await mkdir(getStateDir(), { recursive: true });
  const next = {
    ...state,
    lastUpdatedAt: touch || typeof state.lastUpdatedAt !== "number"
      ? Date.now()
      : state.lastUpdatedAt,
  };
  // Atomic write (tmpfile + rename) so a crash mid-write can't leave a
  // truncated/corrupt state file. See DESIGN.md "State file schema".
  const final = statePath(state.codexSessionId);
  const tmp = `${final}.tmp`;
  await writeFile(tmp, JSON.stringify(next));
  await rename(tmp, final);
}

export async function clearState(codexSessionId) {
  try {
    await rm(statePath(codexSessionId), { force: true });
  } catch { /* best effort */ }
  await clearEnded(codexSessionId);
}

/**
 * Record that the codex thread ended. Written lock-free by the SessionEnd
 * parent. The returned timestamp is the marker's identity: workers carry it as
 * a token and only act on a marker that still matches it.
 *
 * `Date.now()` is only the starting point. Two exits within the same
 * millisecond would otherwise share a marker path, and the first one's
 * conditional removal would take the second one's marker with it, so the
 * timestamp is bumped until an exclusive create succeeds. Bumping rather than
 * randomizing keeps the names ordered, which is what `before` compares.
 */
export async function markEnded(codexSessionId) {
  if (!codexSessionId) return 0;
  let ts = Date.now();
  try {
    await mkdir(getStateDir(), { recursive: true });
    while (true) {
      try {
        await writeFile(endedPath(codexSessionId, ts), String(ts), { flag: "wx" });
        break;
      } catch (err) {
        if (err?.code !== "EEXIST") throw err;
        ts += 1;
      }
    }
  } catch { /* best effort */ }
  return ts;
}

/**
 * Drop the end marker: the thread is alive again, or its commit succeeded.
 *
 * `before` makes the removal conditional: a marker at or after that timestamp
 * belongs to a later exit than the caller and is left in place, so a late Stop
 * worker cannot erase a fresh marker.
 */
export async function clearEnded(codexSessionId, { before } = {}) {
  if (!codexSessionId) return;
  const cutoff = typeof before === "number" ? before : Infinity;
  const dir = getStateDir();
  for (const marker of await endedMarkers(codexSessionId)) {
    if (marker.ts >= cutoff) continue;
    // Each marker path carries its own timestamp, so removing one can never
    // take out a marker a later exit wrote.
    await rm(join(dir, marker.file), { force: true }).catch(() => {});
  }
  const legacy = await legacyEndedAt(codexSessionId);
  if (legacy !== undefined && legacy < cutoff) {
    // Pre-0.8.1 leftover: still read-then-remove, races included.
    await rm(legacyEndedPath(codexSessionId), { force: true }).catch(() => {});
  }
}

export async function readEndedAt(codexSessionId) {
  try {
    return await endedAtFrom(await readdir(getStateDir()), codexSessionId);
  } catch {
    return undefined;
  }
}

const LOCK_POLL_MS = 100;

/**
 * Take a stale lock over, atomically, against every other taker that saw the
 * same dead lock.
 *
 * The takers cannot race for the `owner` file itself: displacing it would
 * happily unseat a taker that won a moment earlier and is already running, and
 * any scheme that moves it aside leaves the stamp momentarily absent for a
 * third taker to claim. Instead each taker derives a claim directory from the
 * exact state it read (`seen`, or its absence) and races to `mkdir` it, which
 * is atomic: everyone who observed the same dead lock contends for one name,
 * exactly one wins, and the losers re-read a lock that now carries the
 * winner's fresh stamp. The winner drops the claim as soon as it is stamped,
 * so later generations can take over in turn; a claim left behind by a taker
 * that died mid-takeover ages out like the lock itself.
 */
async function claimStaleLock(dir, ownerFile, token, seen, staleMs) {
  const key = createHash("sha256").update(seen ?? "\u0000unstamped").digest("hex").slice(0, 32);
  const claimDir = join(dir, `claim-${key}`);
  try {
    await mkdir(claimDir);
  } catch (err) {
    if (err?.code === "EEXIST") {
      try {
        if (Date.now() - (await stat(claimDir)).mtimeMs > staleMs) {
          await rmdir(claimDir).catch(() => {});
        }
      } catch {}
    }
    return false;
  }
  await writeFile(ownerFile, token);
  const now = new Date();
  await utimes(dir, now, now).catch(() => {});
  await rmdir(claimDir).catch(() => {});
  return true;
}

/**
 * Run `fn` while holding an exclusive per-session lock.
 *
 * The lock is a directory (mkdir is atomic everywhere we run). A lock whose
 * `owner` stamp — or, before one is written, whose directory — is older than
 * `staleMs` is abandoned, so a killed holder cannot wedge a session forever; a
 * live holder keeps both fresh through `heartbeat()`.
 * `waitMs: 0` makes this a try-lock that returns `{ skipped: true }` instead
 * of waiting. Callers must always load state *inside* `fn`.
 *
 * The holder stamps an `owner` file inside the directory and only ever
 * releases (or refreshes) a lock whose owner is still its own, so a stale
 * takeover cannot make one taker drop the lock another taker now holds.
 * Takeover never removes or moves the directory or the stamp — neither path
 * may be momentarily absent, or a racer would claim the lock alongside the
 * taker. Instead the takers race for a claim directory; see `claimStaleLock`.
 */
export async function withSessionLock(codexSessionId, fn, { waitMs = 0, staleMs = 300_000 } = {}) {
  const dir = lockPath(codexSessionId);
  const ownerFile = join(dir, "owner");
  const token = `${process.pid}:${randomUUID()}`;
  await mkdir(getStateDir(), { recursive: true });
  const deadline = Date.now() + Math.max(0, waitMs);
  let held = false;
  while (true) {
    try {
      await mkdir(dir);
      await writeFile(ownerFile, token);
      held = true;
      break;
    } catch (err) {
      if (err?.code !== "EEXIST") throw err;
      // Age the lock by its own stamp when it has one: a taker that just won
      // the stamp is live even before it refreshes the directory, and reading
      // the directory here would let the losers of that race take the lock
      // straight back off the winner.
      let seen = null;
      try {
        seen = await readFile(ownerFile, "utf-8");
      } catch {}
      let ageMs;
      try {
        ageMs = Date.now() - (await stat(seen === null ? dir : ownerFile)).mtimeMs;
      } catch {
        continue; // holder released between mkdir and stat; retry immediately
      }
      if (ageMs > staleMs) {
        if (await claimStaleLock(dir, ownerFile, token, seen, staleMs)) {
          held = true;
          break;
        }
        // Another taker claimed this generation; give it a moment to stamp the
        // lock before re-reading, or we would spin on the state it replaces.
        if (Date.now() >= deadline) break;
        await new Promise((resolve) => setTimeout(resolve, LOCK_POLL_MS));
        continue;
      }
      if (Date.now() >= deadline) break;
      await new Promise((resolve) => setTimeout(resolve, LOCK_POLL_MS));
    }
  }
  if (!held) return { skipped: true };
  const owned = async () => {
    try {
      return (await readFile(ownerFile, "utf-8")) === token;
    } catch {
      return false;
    }
  };
  const heartbeat = async () => {
    if (!(await owned())) return;
    const now = new Date();
    await utimes(ownerFile, now, now).catch(() => {});
    await utimes(dir, now, now).catch(() => {});
  };
  try {
    return { skipped: false, value: await fn({ heartbeat }) };
  } finally {
    if (await owned()) {
      await rm(ownerFile, { force: true }).catch(() => {});
      // Recursive: a taker that died mid-takeover can leave its claim
      // directory behind, and a plain rmdir would then leave the whole lock
      // standing with a fresh mtime for a full `staleMs`.
      await rm(dir, { recursive: true, force: true }).catch(() => {});
    }
  }
}

export async function listStates() {
  try {
    const dir = getStateDir();
    const files = await readdir(dir);
    const out = [];
    for (const file of files) {
      // .json only — atomic writes briefly create `<id>.json.tmp`, skipped
      // by this check (endsWith(".json") is false for ".json.tmp").
      if (!file.endsWith(".json")) continue;
      try {
        const raw = await readFile(join(dir, file), "utf-8");
        const parsed = JSON.parse(raw);
        if (!parsed?.codexSessionId) continue;
        // Resolve the marker from the listing we already have instead of
        // re-reading the directory once per state file.
        const endedAt = await endedAtFrom(files, parsed.codexSessionId);
        out.push(endedAt ? { ...parsed, endedAt } : parsed);
      } catch { /* skip */ }
    }
    return out;
  } catch {
    return [];
  }
}
