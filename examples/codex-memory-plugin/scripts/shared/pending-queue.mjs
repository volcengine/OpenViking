// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
/**
 * Local pending queue for offline resilience.
 *
 * When the OpenViking server is temporarily unreachable, write operations
 * (addMessage, commitSession) serialize their payloads to
 * `~/.openviking/pending/` as JSON files. The queue is replayed in small
 * batches, either at session-start (consuming retry budgets, with
 * maxRetries/TTL) or by a long-running drainer that passes
 * `consumeRetries: false` so transient failures stay retryable.
 *
 * Each file contains: { type, sessionId, payload, createdAt, retries, dedupKey }
 *
 * Config (env vars):
 *   OPENVIKING_PENDING_DIR         pending queue directory
 *                                  (default: ~/.openviking/pending)
 *   OPENVIKING_PENDING_MAX_RETRIES max retry attempts per item (default: 3)
 *   OPENVIKING_PENDING_TTL_DAYS    max age in days before stale cleanup
 *                                  (default: 7)
 *   OPENVIKING_PENDING_REPLAY_LIMIT max items replayed per session-start
 *                                  (default: 50)
 */

import { mkdir, readdir, readFile, rename, writeFile, unlink, stat, chmod, utimes } from "node:fs/promises";
import { createHash } from "node:crypto";
import { join } from "node:path";
import { homedir } from "node:os";
import { isDeferredFailure, isRetryableFailure } from "./retryable.mjs";

const DEFAULT_MAX_RETRIES = 3;
const DEFAULT_TTL_DAYS = 7;
const DEFAULT_REPLAY_LIMIT = 50;
const PROCESSING_STALE_MS = 10 * 60 * 1000;
const DEFAULT_PENDING_DIR = () => join(homedir(), ".openviking", "pending");

// Pending queue files may contain raw memory payload / transcript content.
// Use restrictive permissions explicitly so we don't depend on umask.
//   - dir: 0o700  (owner: rwx; group/other: none)
//   - file: 0o600 (owner: rw;  group/other: none)
const PENDING_DIR_MODE = 0o700;
const PENDING_FILE_MODE = 0o600;

/**
 * Ensure the pending directory exists with 0o700 permissions. If the
 * directory was created by a previous version of this code (or by hand)
 * with looser permissions, best-effort chmod it on first use.
 */
async function ensurePendingDir(dir) {
  await mkdir(dir, { recursive: true, mode: PENDING_DIR_MODE });
  try {
    await chmod(dir, PENDING_DIR_MODE);
  } catch {
    // Best effort: chmod may fail on some platforms (e.g. Windows); not fatal.
  }
}

function getPendingDir() {
  return process.env.OPENVIKING_PENDING_DIR || DEFAULT_PENDING_DIR();
}

function getMaxRetries() {
  const v = parseInt(process.env.OPENVIKING_PENDING_MAX_RETRIES || "", 10);
  return Number.isFinite(v) && v >= 0 ? v : DEFAULT_MAX_RETRIES;
}

function getTTLDays() {
  const v = parseInt(process.env.OPENVIKING_PENDING_TTL_DAYS || "", 10);
  return Number.isFinite(v) && v >= 0 ? v : DEFAULT_TTL_DAYS;
}

function getReplayLimit() {
  const v = parseInt(process.env.OPENVIKING_PENDING_REPLAY_LIMIT || "", 10);
  return Number.isFinite(v) && v > 0 ? v : DEFAULT_REPLAY_LIMIT;
}

function stableStringify(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  const keys = Object.keys(value).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${stableStringify(value[k])}`).join(",")}}`;
}

function makeDedupKey(type, sessionId, payload) {
  return createHash("sha256")
    .update(type)
    .update("\n")
    .update(sessionId)
    .update("\n")
    .update(stableStringify(payload))
    .digest("hex");
}

function pendingFilename(dedupKey, retries = 0) {
  return `${dedupKey}_${Math.max(0, Number(retries) || 0)}.json`;
}

function retryFilename(filename, retries) {
  const bare = filename.replace(/\.(json|processing)$/, "");
  const nextBare = /_\d+$/.test(bare)
    ? bare.replace(/_\d+$/, `_${retries}`)
    : `${bare}_${retries}`;
  return `${nextBare}.json`;
}

function processingFilename(filename) {
  return filename.replace(/\.json$/, ".processing");
}

function pendingFromProcessingFilename(filename) {
  return filename.replace(/\.processing$/, ".json");
}

function isRetryableReplayFailure(res) {
  return isRetryableFailure(res);
}

function isDeferredReplayFailure(res) {
  return isDeferredFailure(res);
}

async function readEntry(dir, filename) {
  const raw = await readFile(join(dir, filename), "utf-8");
  return JSON.parse(raw);
}

async function findExistingByDedupKey(dir, dedupKey) {
  let files;
  try {
    files = await readdir(dir);
  } catch {
    return null;
  }

  // Fast path: filenames are `${dedupKey}_${retries}.json` (or
  // .processing). `dedupKey` is a sha256 hex produced by makeDedupKey
  // (no underscores), so `${dedupKey}_` is a unique prefix per key.
  // Filter on prefix BEFORE opening the file to avoid readFile +
  // JSON.parse on every entry in the directory on every enqueue call —
  // the previous implementation was O(n²) in queue size. Worst case here
  // is O(n) readdir entries, but only the (typically single) prefix
  // match actually gets opened and parsed.
  const prefix = `${dedupKey}_`;

  for (const f of files) {
    if (!f.startsWith(prefix)) continue;
    if (!f.endsWith(".json") && !f.endsWith(".processing")) continue;
    try {
      const entry = await readEntry(dir, f);
      if (entry?.dedupKey === dedupKey) return { filename: f, entry };
    } catch {
      // Corrupted file - ignore for dedup lookup.
    }
  }
  return null;
}

async function recoverStaleProcessing(dir) {
  let files;
  try {
    files = await readdir(dir);
  } catch {
    return 0;
  }

  const now = Date.now();
  let recovered = 0;
  for (const f of files) {
    if (!f.endsWith(".processing")) continue;
    const from = join(dir, f);
    try {
      const s = await stat(from);
      if (now - s.mtimeMs < PROCESSING_STALE_MS) continue;
      const to = join(dir, pendingFromProcessingFilename(f));
      await rename(from, to);
      recovered++;
    } catch {
      // Best effort. A concurrent process may have already handled it.
    }
  }
  return recovered;
}

/**
 * Enqueue a failed operation to local disk.
 *
 * @param {string} type - "addMessage" or "commitSession"
 * @param {string} sessionId - OV session ID
 * @param {object} payload - the data that failed to send
 * @param {object} options
 * @param {number} options.createdAt - optional queue timestamp override
 */
export async function enqueue(type, sessionId, payload, options = {}) {
  const dir = getPendingDir();
  const now = Number.isFinite(options.createdAt) ? options.createdAt : Date.now();
  const dedupKey = makeDedupKey(type, sessionId, payload);
  const filename = pendingFilename(dedupKey, 0);
  const entry = {
    type,
    sessionId,
    payload,
    createdAt: now,
    retries: 0,
    dedupKey,
  };

  try {
    await ensurePendingDir(dir);
  } catch (err) {
    return { ok: false, error: err?.message || String(err), dedupKey };
  }

  const existing = await findExistingByDedupKey(dir, dedupKey);
  if (existing) {
    return { ok: true, path: existing.filename, deduped: true, dedupKey };
  }

  try {
    await writeFile(join(dir, filename), JSON.stringify(entry), {
      encoding: "utf-8",
      flag: "wx",
      mode: PENDING_FILE_MODE,
    });
    return { ok: true, path: filename, dedupKey };
  } catch (err) {
    if (err?.code !== "EEXIST") {
      return { ok: false, error: err?.message || String(err), dedupKey };
    }
  }

  const duplicate = await findExistingByDedupKey(dir, dedupKey);
  if (duplicate) {
    return { ok: true, path: duplicate.filename, deduped: true, dedupKey };
  }
  return { ok: false, error: `pending file exists but dedup entry was not readable: ${filename}`, dedupKey };
}

/**
 * List all pending queue entries.
 * Returns array of { filename, entry } sorted by createdAt ascending.
 */
export async function listPending() {
  const dir = getPendingDir();
  let files;
  try {
    await recoverStaleProcessing(dir);
    files = await readdir(dir);
  } catch {
    return [];
  }

  const entries = [];
  for (const f of files) {
    if (!f.endsWith(".json")) continue;
    try {
      const entry = await readEntry(dir, f);
      entries.push({ filename: f, entry });
    } catch {
      // Corrupted file - skip.
    }
  }

  entries.sort((a, b) => (a.entry.createdAt || 0) - (b.entry.createdAt || 0));
  return entries;
}

/**
 * List the queue without touching it.
 *
 * `listPending` recovers stale `.processing` files as a side effect, which is
 * right for a hook about to replay and wrong for anything diagnostic: a doctor
 * that renames a claim while its owner is still working would create the
 * duplicate it exists to report.
 */
export async function listPendingReadOnly() {
  const dir = getPendingDir();
  let files;
  try {
    files = await readdir(dir);
  } catch {
    return [];
  }
  const entries = [];
  for (const f of files) {
    if (!f.endsWith(".json")) continue;
    try {
      entries.push({ filename: f, entry: await readEntry(dir, f) });
    } catch {
      // Corrupted file - skip.
    }
  }
  entries.sort((a, b) => (a.entry.createdAt || 0) - (b.entry.createdAt || 0));
  return entries;
}

/**
 * Atomically claim a pending file for replay. Only the process that successfully
 * renames the file may send the HTTP replay.
 */
export async function claimForReplay(filename) {
  if (!filename.endsWith(".json")) return null;
  const dir = getPendingDir();
  const claimed = processingFilename(filename);
  try {
    // Recovery measures claim age, not how long the item waited offline.
    // Refresh before publishing .processing so recovery cannot see an old mtime.
    const now = new Date();
    await utimes(join(dir, filename), now, now);
    await rename(join(dir, filename), join(dir, claimed));
    return claimed;
  } catch {
    return null;
  }
}

/**
 * Release a claimed file back to the queue without consuming a retry. The
 * entry keeps its original filename, retry count, and createdAt position, so a
 * later run retries it as if this attempt never happened.
 */
export async function releaseClaim(claimedFilename) {
  if (!claimedFilename.endsWith(".processing")) return null;
  const dir = getPendingDir();
  const restored = pendingFromProcessingFilename(claimedFilename);
  try {
    await rename(join(dir, claimedFilename), join(dir, restored));
    return restored;
  } catch {
    return null;
  }
}

/**
 * Remove a pending entry after successful replay.
 */
export async function dequeue(filename) {
  const dir = getPendingDir();
  try {
    await unlink(join(dir, filename));
    return true;
  } catch {
    return false;
  }
}

/**
 * Record one failed attempt.
 *
 * Three outcomes, because the caller's next move differs for each:
 *
 *   "kept"    the entry is back in the queue with one more retry used
 *   "dropped" the retry budget is spent and the entry was removed — the data is
 *             gone and the caller must record the loss
 *   "failed"  the retry could not be written; the claimed file is still on disk
 *             and recovery will replay it, so nothing is lost
 *
 * A boolean could not tell "dropped" from "failed", and the replay loop read
 * `false` as a loss — reporting data as lost while it was still queued.
 */
export async function incrementRetryDetailed(filename, entry) {
  const dir = getPendingDir();
  const maxRetries = getMaxRetries();
  const nextRetries = (entry.retries || 0) + 1;

  if (nextRetries > maxRetries) {
    try {
      await unlink(join(dir, filename));
      return { outcome: "dropped", retries: nextRetries };
    } catch {
      return { outcome: "failed", retries: entry.retries || 0, error: "unlink_failed" };
    }
  }

  const updated = { ...entry, retries: nextRetries };
  const newFilename = retryFilename(filename, nextRetries);
  // Atomic write: write to a unique temp file in the same directory, then
  // rename into place. Avoids leaving a half-written JSON if the process
  // crashes mid-write.
  const tmpFilename = `${newFilename}.tmp.${process.pid}.${Date.now()}`;
  try {
    await writeFile(join(dir, tmpFilename), JSON.stringify(updated), {
      encoding: "utf-8",
      flag: "wx",
      mode: PENDING_FILE_MODE,
    });
    await rename(join(dir, tmpFilename), join(dir, newFilename));
    await unlink(join(dir, filename)).catch(() => {});
    // The caller's copy must show the count that is now on disk.
    entry.retries = nextRetries;
    return { outcome: "kept", retries: nextRetries, filename: newFilename };
  } catch {
    await unlink(join(dir, tmpFilename)).catch(() => {});
    return { outcome: "failed", retries: entry.retries || 0, error: "write_failed" };
  }
}

/** The boolean shape existing callers expect: `true` only when the entry is kept. */
export async function incrementRetry(filename, entry) {
  return (await incrementRetryDetailed(filename, entry)).outcome === "kept";
}

/**
 * Clean up stale entries older than TTL.
 *
 * Returns the entries it dropped, because a queue entry that expires is a
 * message the user was told would be delivered and no longer will be. A caller
 * that tracks delivery state has to record that loss against the right session
 * and dedup key; a bare count cannot say whose data went missing.
 */
export async function cleanStaleDetailed({ now = Date.now(), exclude = null } = {}) {
  const ttlMs = getTTLDays() * 24 * 60 * 60 * 1000;
  const pending = await listPending();
  const lost = [];
  // A caller may hold entries on purpose (a resumed generation's commit). The
  // set holds dedup keys, and an entry is matched by key *or* filename: a queue
  // entry written by an older client may not carry a key at all, and matching
  // only on the key would expire exactly the item the caller asked to keep.
  const held = exclude instanceof Set ? exclude : null;
  const isHeld = (entry, filename) => Boolean(held)
    && ((entry?.dedupKey && held.has(entry.dedupKey)) || held.has(filename));

  for (const { filename, entry } of pending) {
    if (isHeld(entry, filename)) continue;
    const age = now - (entry.createdAt || 0);
    if (age <= ttlMs) continue;
    // One shape for a loss everywhere: the bare entry. A caller that reads
    // `sessionId`/`dedupKey` must not have to unwrap a different wrapper for
    // each API that can drop something.
    if (await dequeue(filename)) lost.push({ ...entry, filename });
  }
  return { cleaned: lost.length, lost };
}

/** The number of expired entries removed, as callers written before `lost` expect. */
export async function cleanStale() {
  return (await cleanStaleDetailed()).cleaned;
}

function errorKindFor(res, fallback) {
  if (!res) return fallback;
  if (res.ok) return undefined;
  if (res.status) return `http_${res.status}`;
  if (res.error?.code) return String(res.error.code);
  return fallback;
}

/**
 * Replay pending entries, one explained result per queue operation.
 *
 * `replayPending` used to answer with four counters, and a caller that had to
 * clear a `commitPending` could not tell an `addMessage` from a `commitSession`,
 * a delivered item from one the queue dropped at its TTL, or its own session's
 * item from another's. The counters are still here — every existing caller
 * keeps working — but each operation now also reports what became of it:
 *
 *   sent      the server confirmed it; safe to advance and settle
 *   failed    it was attempted and did not land; still queued for retry
 *   skipped   the queue dropped it (TTL, retries exhausted, non-retryable
 *             status, unknown type); the data is gone and must be recorded
 *   deferred  not attempted this run: `shouldReplay` said wait, the replay
 *             limit was reached, another process claimed it, or an earlier
 *             `addMessage` failure stopped the pass
 *
 * `shouldReplay(entry)` runs before the claim, so a deferred item keeps its
 * retry budget and its place in the queue. `claimForReplay` losing a race is
 * `deferred`, not `skipped`: another hook owns that item, and calling it lost
 * would make the adapter record a `pending_lost` for data that is still on its
 * way.
 */
export async function replayPendingDetailed(fetchJSON, log, options = {}) {
  const consumeRetries = options.consumeRetries !== false;
  const shouldReplay = typeof options.shouldReplay === "function" ? options.shouldReplay : null;
  const results = [];
  const counts = { replayed: 0, failed: 0, skipped: 0, deferred: 0 };

  const record = (entry, status, extra = {}) => {
    results.push({
      type: entry?.type,
      sessionId: entry?.sessionId,
      dedupKey: entry?.dedupKey,
      status,
      ...extra,
    });
  };

  const pending = await listPending();
  if (pending.length === 0) {
    return { ...counts, results, cleaned: 0, lost: [] };
  }

  const replayLimit = getReplayLimit();
  log("pending-queue", { count: pending.length, replayLimit, action: "replay-start" });

  const ttlMs = getTTLDays() * 24 * 60 * 60 * 1000;
  const nowMs = Date.now();
  let processed = 0;
  // Per-session, not global: the queue's order only means something inside one
  // OpenViking session. One session's stuck message must not hold back another
  // session's, which is what the old global stop did.
  const stoppedSessions = new Set();
  const lost = [];
  // Entries a caller's predicate deliberately held. They are deferred by
  // decision, not by backlog, so the TTL sweep below must not expire them.
  const predicateHeld = new Set();

  for (let index = 0; index < pending.length; index += 1) {
    const { filename, entry } = pending[index];
    const sessionId = entry?.sessionId;

    // The predicate runs before every destructive decision. A caller that is
    // deliberately holding an item (a resumed generation deferring its
    // predecessor's commit) must not have it expire or burn a retry while it
    // waits.
    if (shouldReplay) {
      let verdict;
      try {
        verdict = await shouldReplay(entry);
      } catch {
        verdict = false;
      }
      if (!verdict) {
        record(entry, "deferred", { errorKind: "predicate" });
        counts.deferred++;
        // Hold it by both identities: the key when there is one, and the
        // filename so an older entry without a key is not swept either.
        if (entry?.dedupKey) predicateHeld.add(entry.dedupKey);
        predicateHeld.add(filename);
        continue;
      }
    }

    if (stoppedSessions.has(sessionId)) {
      record(entry, "deferred", { errorKind: "backlog_after_failure" });
      counts.deferred++;
      continue;
    }

    if (processed >= replayLimit) {
      record(entry, "deferred", { errorKind: "replay_limit" });
      counts.deferred++;
      continue;
    }

    if ((entry.retries || 0) >= getMaxRetries()) {
      // A drop that fails to delete is not a drop. Reporting `skipped` would
      // tell the adapter the data is gone while it is still on disk.
      const removed = await dequeue(filename);
      record(entry, removed ? "skipped" : "failed", {
        errorKind: removed ? "retries_exhausted" : "dequeue_failed",
        retries: entry.retries || 0,
      });
      if (removed) {
        counts.skipped++;
        lost.push(entry);
      } else {
        counts.failed++;
      }
      continue;
    }

    if (nowMs - (Number(entry.createdAt) || 0) > ttlMs) {
      const removed = await dequeue(filename);
      record(entry, removed ? "skipped" : "failed", {
        errorKind: removed ? "ttl_expired" : "dequeue_failed",
      });
      if (removed) {
        counts.skipped++;
        lost.push(entry);
      } else {
        counts.failed++;
      }
      continue;
    }

    const claimedFilename = await claimForReplay(filename);
    if (!claimedFilename) {
      // Someone else owns it, or it disappeared between the listing and now.
      record(entry, "deferred", { errorKind: "claim_contended" });
      counts.deferred++;
      continue;
    }
    processed++;

    let res;
    try {
      const encodedSid = encodeURIComponent(entry.sessionId);
      if (entry.type === "addMessage") {
        res = await fetchJSON(`/api/v1/sessions/${encodedSid}/messages`, {
          method: "POST",
          body: JSON.stringify(entry.payload),
        });
      } else if (entry.type === "commitSession") {
        res = await fetchJSON(`/api/v1/sessions/${encodedSid}/commit`, {
          method: "POST",
          body: JSON.stringify(entry.payload || {}),
        });
      } else {
        const removed = await dequeue(claimedFilename);
        record(entry, removed ? "skipped" : "failed", {
          errorKind: removed ? "unknown_type" : "dequeue_failed",
        });
        if (removed) {
          counts.skipped++;
          lost.push(entry);
        } else {
          counts.failed++;
        }
        continue;
      }
    } catch {
      res = { ok: false };
    }

    if (entry.type === "commitSession") {
      log("pending-queue", {
        action: "commit-replay",
        sessionId: entry.sessionId,
        ok: Boolean(res?.ok),
        status: res?.result?.status || res?.status,
        trace_id: res?.traceId || res?.result?.trace_id,
        error: res?.ok ? undefined : res?.error?.message || res?.error?.code,
      });
    }

    if (res?.ok) {
      // A send that landed but whose queue entry could not be removed is
      // `sent` with a warning: the data reached the server, and the duplicate
      // risk is the honest cost of a failed delete.
      const removed = await dequeue(claimedFilename);
      record(entry, "sent", {
        statusCode: res?.result?.status || res?.status,
        ...(removed ? {} : { dequeueFailed: true }),
      });
      counts.replayed++;
      continue;
    }

    if (isDeferredReplayFailure(res)) {
      // The hook ran out of time before it could ask. Release the claim so the
      // next boundary retries with a fresh budget, and spend nothing.
      await releaseClaim(claimedFilename);
      record(entry, "deferred", { errorKind: "deadline_exceeded" });
      counts.deferred++;
      if (entry.type === "addMessage" && sessionId) stoppedSessions.add(sessionId);
      continue;
    }

    if (!isRetryableReplayFailure(res)) {
      const removed = await dequeue(claimedFilename);
      record(entry, removed ? "skipped" : "failed", {
        errorKind: removed ? errorKindFor(res, "non_retryable_failure") : "dequeue_failed",
        statusCode: res?.status,
      });
      if (removed) {
        counts.skipped++;
        lost.push(entry);
      } else {
        counts.failed++;
      }
      continue;
    }

    let retries = entry.retries || 0;
    if (consumeRetries) {
      const retry = await incrementRetryDetailed(claimedFilename, entry);
      if (retry.outcome === "dropped") {
        // The budget is spent and the entry is gone: a real loss.
        record(entry, "skipped", {
          errorKind: "retries_exhausted",
          statusCode: res?.status,
          retries: retry.retries,
        });
        counts.skipped++;
        lost.push(entry);
        continue;
      }
      if (retry.outcome === "failed") {
        // The claim is still on disk and recovery will replay it. Nothing is
        // lost, so this is a failure — not a drop.
        record(entry, "failed", {
          errorKind: "retry_write_failed",
          statusCode: res?.status,
          retries,
        });
        counts.failed++;
        if (entry.type === "addMessage" && sessionId) stoppedSessions.add(sessionId);
        continue;
      }
      retries = retry.retries;
    } else {
      const released = await releaseClaim(claimedFilename);
      log("pending-queue", {
        action: released ? "replay-deferred" : "release-failed",
        sessionId: entry.sessionId,
        type: entry.type,
        status: res?.result?.status || res?.status,
        retries: entry.retries || 0,
      });
    }
    record(entry, "failed", {
      errorKind: errorKindFor(res, "retryable_failure"),
      statusCode: res?.status,
      // The count that is actually on disk. `incrementRetryDetailed` already
      // wrote it; adding one here reported a budget the queue did not have.
      retries,
    });
    counts.failed++;
    // A message that could not be delivered holds back the rest of *its own*
    // session: the queue order inside a session is what keeps a transcript
    // from having a hole. Other sessions are independent and keep going.
    if (entry.type === "addMessage" && sessionId) stoppedSessions.add(sessionId);
  }

  // Expired entries must still leave the queue even when this pass skipped them
  // for backlog or the replay limit — otherwise a stuck session keeps a
  // transcript on disk past its TTL. A predicate-held entry is the exception:
  // the caller asked for it to wait, so its age is not the reason to drop it.
  const stale = await cleanStaleDetailed({ now: nowMs, exclude: predicateHeld });
  const alreadyReported = new Set(lost.map((item) => item.dedupKey));
  for (const entry of stale.lost) {
    if (entry?.dedupKey && alreadyReported.has(entry.dedupKey)) continue;
    record(entry, "skipped", { errorKind: "ttl_expired" });
    counts.skipped++;
    lost.push(entry);
  }

  log("pending-queue", {
    action: "replay-done",
    replayed: counts.replayed,
    failed: counts.failed,
    skipped: counts.skipped,
    deferred: counts.deferred,
    cleaned: lost.length,
  });

  return { ...counts, results, cleaned: lost.length, lost };
}

/**
 * Replay pending entries. Call this during session-start when the server is
 * healthy. Each run processes at most OPENVIKING_PENDING_REPLAY_LIMIT items so
 * a just-recovered server is not hit with an unbounded replay burst.
 *
 * @param {Function} fetchJSON - the configured fetchJSON from makeFetchJSON
 * @param {Function} log - logger function
 * @param {object} [options]
 * @param {boolean} [options.consumeRetries=true] - when false, retryable
 *   failures release their claim instead of incrementing the retry count, so
 *   a background drainer can keep retrying a transient failure without
 *   burning the session-start retry budget. Exhausted and non-retryable
 *   entries are deleted exactly as in the default mode.
 * @param {Function} [options.shouldReplay] - `(entry) => boolean`; a false
 *   answer defers that operation without claiming it or spending a retry.
 * @returns {{ replayed: number, failed: number, skipped: number, deferred: number,
 *   results: Array<{type: string, sessionId: string, dedupKey: string,
 *   status: "sent"|"failed"|"skipped"|"deferred", errorKind?: string}>,
 *   cleaned: number, lost: Array<object> }}
 */
export async function replayPending(fetchJSON, log, options = {}) {
  const { replayed, failed, skipped, deferred } = await replayPendingDetailed(fetchJSON, log, options);
  return { replayed, failed, skipped, deferred };
}
