/**
 * Local pending queue for offline resilience.
 *
 * When the OpenViking server is temporarily unreachable, write operations
 * (addMessage, commitSession) serialize their payloads to
 * `~/.openviking/pending/` as JSON files. The queue is replayed in bounded
 * runs, either at session-start (consuming retry budgets, with
 * maxRetries/TTL) or by a long-running drainer that passes
 * `consumeRetries: false` so transient failures stay retryable. A session's
 * queued messages go out through the batch endpoint, so a backlog built up
 * during an outage drains in a few requests instead of one per message.
 *
 * Each file contains: { type, sessionId, payload, createdAt, retries, dedupKey }
 *
 * Config (env vars):
 *   OPENVIKING_PENDING_DIR         pending queue directory
 *                                  (default: ~/.openviking/pending)
 *   OPENVIKING_PENDING_MAX_RETRIES max retry attempts per item (default: 3)
 *   OPENVIKING_PENDING_TTL_DAYS    max age in days before stale cleanup
 *                                  (default: 7)
 *   OPENVIKING_PENDING_REPLAY_LIMIT max items replayed per run
 *                                  (default: 500)
 */

import { mkdir, readdir, readFile, rename, writeFile, unlink, stat, chmod, utimes } from "node:fs/promises";
import { createHash } from "node:crypto";
import { join } from "node:path";
import { homedir } from "node:os";
import { isRetryableFailure } from "./retryable.mjs";

const DEFAULT_MAX_RETRIES = 3;
const DEFAULT_TTL_DAYS = 7;
const DEFAULT_REPLAY_LIMIT = 500;
const PROCESSING_STALE_MS = 10 * 60 * 1000;
const REPLAY_TYPES = new Set(["addMessage", "commitSession"]);

// Messages per request on `/api/v1/sessions/{id}/messages/batch`.
export const BATCH_LIMIT = 100;
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
 * Increment retry count on a pending entry. Returns false if max retries exceeded.
 */
export async function incrementRetry(filename, entry) {
  const dir = getPendingDir();
  const maxRetries = getMaxRetries();
  entry.retries = (entry.retries || 0) + 1;

  if (entry.retries > maxRetries) {
    try {
      await unlink(join(dir, filename));
    } catch {
      // Best effort.
    }
    return false;
  }

  const newFilename = retryFilename(filename, entry.retries);
  // Atomic write: write to a unique temp file in the same directory, then
  // rename into place. Avoids leaving a half-written JSON if the process
  // crashes mid-write.
  const tmpFilename = `${newFilename}.tmp.${process.pid}.${Date.now()}`;
  try {
    await writeFile(join(dir, tmpFilename), JSON.stringify(entry), {
      encoding: "utf-8",
      flag: "wx",
      mode: PENDING_FILE_MODE,
    });
    await rename(join(dir, tmpFilename), join(dir, newFilename));
    await unlink(join(dir, filename)).catch(() => {});
    return true;
  } catch {
    await unlink(join(dir, tmpFilename)).catch(() => {});
    return false;
  }
}

/**
 * Clean up stale entries older than TTL.
 */
export async function cleanStale() {
  const ttlMs = getTTLDays() * 24 * 60 * 60 * 1000;
  const now = Date.now();
  const pending = await listPending();
  let cleaned = 0;

  for (const { filename, entry } of pending) {
    const age = now - (entry.createdAt || 0);
    if (age > ttlMs) {
      await dequeue(filename);
      cleaned++;
    }
  }
  return cleaned;
}

/**
 * Group the queue into replay runs. Sessions are independent on the server,
 * so order only has to hold within one session: its addMessage entries form
 * batches of up to BATCH_LIMIT, and a commitSession entry closes the batch
 * queued before it so the commit sees those messages. Runs keep the order in
 * which their first entry was queued.
 */
function planRuns(pending) {
  const runs = [];
  const open = new Map();
  for (const item of pending) {
    const { type, sessionId } = item.entry;
    if (type !== "addMessage") {
      open.delete(sessionId);
      runs.push({ items: [item] });
      continue;
    }
    let run = open.get(sessionId);
    if (!run) {
      run = { items: [] };
      open.set(sessionId, run);
      runs.push(run);
    }
    run.items.push(item);
    if (run.items.length >= BATCH_LIMIT) open.delete(sessionId);
  }
  return runs;
}

async function post(fetchJSON, path, payload) {
  try {
    return await fetchJSON(path, { method: "POST", body: JSON.stringify(payload) });
  } catch {
    return { ok: false };
  }
}

/**
 * Park a run the server could not take right now. Only the head consumes a
 * retry, as a single-entry replay did; the rest keep their budget and go out
 * once the head is delivered or dropped, so one failure against a flapping
 * server cannot burn a whole batch's retries.
 */
async function deferRun(run, consumeRetries, log, res) {
  const [head, ...rest] = run;
  if (consumeRetries) {
    await incrementRetry(head.filename, head.entry);
  } else {
    const released = await releaseClaim(head.filename);
    log("pending-queue", {
      action: released ? "replay-deferred" : "release-failed",
      sessionId: head.entry.sessionId,
      type: head.entry.type,
      status: res?.result?.status || res?.status,
      retries: head.entry.retries || 0,
    });
  }
  for (const { filename } of rest) {
    await releaseClaim(filename);
  }
}

/**
 * Send one claimed run. Returns true when replay must stop because the server
 * could not take messages right now, so later runs would fail the same way.
 */
async function replayRun(fetchJSON, log, run, counts, consumeRetries) {
  const { type, sessionId } = run[0].entry;
  const base = `/api/v1/sessions/${encodeURIComponent(sessionId)}`;

  if (type === "addMessage" && run.length > 1) {
    const res = await post(fetchJSON, `${base}/messages/batch`, {
      messages: run.map(({ entry }) => entry.payload),
    });
    if (res?.ok) {
      for (const { filename } of run) await dequeue(filename);
      counts.replayed += run.length;
      return false;
    }
    if (isRetryableReplayFailure(res)) {
      await deferRun(run, consumeRetries, log, res);
      counts.failed += run.length;
      return true;
    }
    // A server without the batch endpoint (404/405), or one message the server
    // rejects, fails the whole batch. One by one, only that message is dropped.
  }

  for (let i = 0; i < run.length; i++) {
    const { filename, entry } = run[i];
    const res = type === "commitSession"
      ? await post(fetchJSON, `${base}/commit`, entry.payload || {})
      : await post(fetchJSON, `${base}/messages`, entry.payload);

    if (type === "commitSession") {
      log("pending-queue", {
        action: "commit-replay",
        sessionId,
        ok: Boolean(res?.ok),
        status: res?.result?.status || res?.status,
        trace_id: res?.traceId || res?.result?.trace_id,
        error: res?.ok ? undefined : res?.error?.message || res?.error?.code,
      });
    }

    if (res?.ok) {
      await dequeue(filename);
      counts.replayed++;
    } else if (!isRetryableReplayFailure(res)) {
      await dequeue(filename);
      counts.skipped++;
    } else {
      const rest = run.slice(i);
      await deferRun(rest, consumeRetries, log, res);
      counts.failed += rest.length;
      return type === "addMessage";
    }
  }
  return false;
}

/**
 * Replay pending entries. Call this when the server is healthy. Each run
 * processes at most OPENVIKING_PENDING_REPLAY_LIMIT entries so a just-recovered
 * server is not hit with an unbounded replay burst.
 *
 * @param {Function} fetchJSON - the configured fetchJSON from makeFetchJSON
 * @param {Function} log - logger function
 * @param {object} [options]
 * @param {boolean} [options.consumeRetries=true] - when false, retryable
 *   failures release their claim instead of incrementing the retry count, so
 *   a background drainer can keep retrying a transient failure without
 *   burning the session-start retry budget. Exhausted and non-retryable
 *   entries are deleted exactly as in the default mode.
 * @returns {{ replayed: number, failed: number, skipped: number, deferred: number }}
 */
export async function replayPending(fetchJSON, log, options = {}) {
  const consumeRetries = options.consumeRetries !== false;
  const pending = await listPending();

  if (pending.length === 0) {
    return { replayed: 0, failed: 0, skipped: 0, deferred: 0 };
  }

  const replayLimit = getReplayLimit();
  const maxRetries = getMaxRetries();
  log("pending-queue", { count: pending.length, replayLimit, action: "replay-start" });

  const counts = { replayed: 0, failed: 0, skipped: 0, deferred: 0 };
  let processed = 0;

  for (const run of planRuns(pending)) {
    const claimed = [];
    for (const { filename, entry } of run.items) {
      if (processed >= replayLimit) break;
      if ((entry.retries || 0) >= maxRetries || !REPLAY_TYPES.has(entry.type)) {
        await dequeue(filename);
        counts.skipped++;
        continue;
      }
      const claimedFilename = await claimForReplay(filename);
      if (!claimedFilename) {
        counts.skipped++;
        continue;
      }
      processed++;
      claimed.push({ filename: claimedFilename, entry });
    }
    if (claimed.length > 0 && (await replayRun(fetchJSON, log, claimed, counts, consumeRetries))) {
      break;
    }
    if (processed >= replayLimit) break;
  }
  counts.deferred = pending.length - counts.replayed - counts.failed - counts.skipped;

  const cleaned = await cleanStale();

  log("pending-queue", { action: "replay-done", ...counts, cleaned });

  return counts;
}
