// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
/**
 * Durable retry queue for OpenViking plugin writes.
 *
 * Queue data is partitioned by producer and authenticated OpenViking target:
 *   <base>/<producer>/<target-hmac>/
 *
 * The HMAC key is stable in normal user-local operation. Tests may point
 * OPENVIKING_QUEUE_SCOPE_KEY_FILE at an environment-scoped 0600 file; the
 * surrounding environment owns deleting that file during cleanup.
 */

import { createHmac, randomBytes } from "node:crypto";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import {
  chmod,
  lstat,
  mkdir,
  readFile,
  readdir,
  rename,
  stat,
  unlink,
  writeFile,
} from "node:fs/promises";

const DEFAULT_MAX_RETRIES = 3;
const DEFAULT_TTL_DAYS = 7;
const DEFAULT_REPLAY_LIMIT = 50;
const PROCESSING_STALE_MS = 10 * 60 * 1000;
const DIR_MODE = 0o700;
const FILE_MODE = 0o600;
const SCOPE_MARKER = Symbol("openviking-pending-queue-scope");

const pendingBaseDir = () =>
  process.env.OPENVIKING_PENDING_DIR ||
  join(homedir(), ".openviking", "pending");
const scopeKeyFile = () =>
  process.env.OPENVIKING_QUEUE_SCOPE_KEY_FILE ||
  join(homedir(), ".openviking", "queue-scope.key");

function envInt(name, fallback, allowZero = true) {
  const value = Number.parseInt(process.env[name] || "", 10);
  return Number.isFinite(value) && (allowZero ? value >= 0 : value > 0)
    ? value
    : fallback;
}

function stableStringify(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  const keys = Object.keys(value).sort();
  return `{${keys.map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(",")}}`;
}

function normalizedTarget({ baseUrl, account = "", user = "", apiKey = "" }) {
  let endpoint;
  try {
    const parsed = new URL(String(baseUrl || "").trim());
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:")
      throw new Error("unsupported protocol");
    if (parsed.username || parsed.password || parsed.search || parsed.hash)
      throw new Error("embedded credentials");
    parsed.pathname = parsed.pathname.replace(/\/+$/, "") || "/";
    endpoint = parsed.toString().replace(/\/$/, "");
  } catch {
    throw new Error(
      "pending queue scope requires an HTTP(S) OpenViking base URL without embedded credentials",
    );
  }
  const identity =
    account || user
      ? `identity:${String(account)}\0${String(user)}`
      : apiKey
        ? `api-key:${String(apiKey)}`
        : "anonymous-local";
  return `${endpoint}\n${identity}`;
}

async function ensureDir(path) {
  await mkdir(path, { recursive: true, mode: DIR_MODE });
  try {
    const info = await lstat(path);
    if (!info.isDirectory() || info.isSymbolicLink())
      throw new Error(`not a private directory: ${path}`);
    await chmod(path, DIR_MODE);
  } catch (error) {
    throw new Error(
      `pending queue directory is unsafe: ${error?.message || error}`,
    );
  }
}

async function loadOrCreateScopeKey() {
  const path = scopeKeyFile();
  await ensureDir(dirname(path));
  try {
    await writeFile(path, randomBytes(32), { flag: "wx", mode: FILE_MODE });
  } catch (error) {
    if (error?.code !== "EEXIST") throw error;
  }
  let info = await lstat(path);
  if (!info.isFile() || info.isSymbolicLink())
    throw new Error("pending queue scope key must be a regular file");
  try {
    await chmod(path, FILE_MODE);
  } catch {
    /* Windows and read-only stores may reject chmod. */
  }
  info = await lstat(path);
  if (process.platform !== "win32" && (info.mode & 0o077) !== 0) {
    throw new Error(
      "pending queue scope key must not be accessible by group or others",
    );
  }
  const key = await readFile(path);
  if (key.length < 32) throw new Error("pending queue scope key is too short");
  return key;
}

/** Create an opaque queue capability for one producer and authenticated target. */
export async function createQueueScope(options = {}) {
  const producer = String(options.producer || "")
    .trim()
    .toLowerCase();
  if (!/^[a-z0-9][a-z0-9._-]*$/.test(producer)) {
    throw new Error("pending queue scope requires a safe producer name");
  }
  const key = await loadOrCreateScopeKey();
  const targetHash = createHmac("sha256", key)
    .update(normalizedTarget(options))
    .digest("hex");
  const dir = join(pendingBaseDir(), producer, targetHash);
  await ensureDir(dir);
  return Object.freeze({ [SCOPE_MARKER]: true, producer, targetHash, dir });
}

function scopeDir(scope) {
  if (!scope || scope[SCOPE_MARKER] !== true || typeof scope.dir !== "string") {
    throw new Error("pending queue operation requires a queue scope");
  }
  return scope.dir;
}

function makeDedupKey(type, sessionId, payload) {
  return createHmac("sha256", "openviking-pending-dedup-v1")
    .update(type)
    .update("\n")
    .update(sessionId)
    .update("\n")
    .update(stableStringify(payload))
    .digest("hex");
}

const pendingFilename = (key, retries = 0) =>
  `${key}_${Math.max(0, Number(retries) || 0)}.json`;
const processingFilename = (filename) =>
  filename.replace(/\.json$/, ".processing");
const pendingFromProcessingFilename = (filename) =>
  filename.replace(/\.processing$/, ".json");
const manualFilename = (dedupKey) => `${dedupKey}.manual`;

function retryFilename(filename, retries) {
  const bare = filename.replace(/\.(json|processing)$/, "");
  return `${/_\d+$/.test(bare) ? bare.replace(/_\d+$/, `_${retries}`) : `${bare}_${retries}`}.json`;
}

function dedupKeyFromFilename(filename) {
  const match = /^([0-9a-f]{64})_(?:\d+)\.(?:json|processing)$/.exec(filename);
  return match?.[1] || "";
}

async function readEntry(dir, filename) {
  return JSON.parse(await readFile(join(dir, filename), "utf-8"));
}

async function hasManualMarker(dir, dedupKey) {
  try {
    await stat(join(dir, manualFilename(dedupKey)));
    return true;
  } catch {
    return false;
  }
}

async function ensureManualMarker(dir, dedupKey) {
  try {
    await writeFile(join(dir, manualFilename(dedupKey)), "manual\n", {
      flag: "wx",
      mode: FILE_MODE,
    });
    return { ok: true, created: true };
  } catch (error) {
    if (error?.code === "EEXIST") return { ok: true, created: false };
    return { ok: false, error: error?.message || String(error) };
  }
}

async function removeManualMarker(dir, dedupKey) {
  if (dedupKey)
    await unlink(join(dir, manualFilename(dedupKey))).catch(() => {});
}

async function findExistingByDedupKey(dir, dedupKey) {
  let files;
  try {
    files = await readdir(dir);
  } catch {
    return null;
  }
  const prefix = `${dedupKey}_`;
  for (const filename of files) {
    if (
      !filename.startsWith(prefix) ||
      (!filename.endsWith(".json") && !filename.endsWith(".processing"))
    )
      continue;
    try {
      const entry = await readEntry(dir, filename);
      if (entry?.dedupKey === dedupKey) return { filename, entry };
    } catch {
      // Corrupt entries never establish deduplication or cleanup authority.
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
  let recovered = 0;
  for (const filename of files) {
    if (!filename.endsWith(".processing")) continue;
    try {
      if (
        Date.now() - (await stat(join(dir, filename))).mtimeMs <
        PROCESSING_STALE_MS
      )
        continue;
      await rename(
        join(dir, filename),
        join(dir, pendingFromProcessingFilename(filename)),
      );
      recovered++;
    } catch {
      // Another process may own or have recovered the entry.
    }
  }
  return recovered;
}

export async function enqueue(scope, type, sessionId, payload, options = {}) {
  const dir = scopeDir(scope);
  const provenance =
    typeof options.provenance === "string" ? options.provenance : "";
  const dedupKey = makeDedupKey(type, sessionId, payload);
  const filename = pendingFilename(dedupKey);
  const entry = {
    type,
    sessionId,
    payload,
    ...(provenance && provenance !== "manual" ? { provenance } : {}),
    createdAt: Date.now(),
    retries: 0,
    dedupKey,
  };

  await ensureDir(dir);
  let markerCreated = false;
  if (provenance === "manual") {
    const marker = await ensureManualMarker(dir, dedupKey);
    if (!marker.ok) return { ok: false, error: marker.error, dedupKey };
    markerCreated = marker.created;
  }

  const existing = await findExistingByDedupKey(dir, dedupKey);
  if (existing)
    return { ok: true, path: existing.filename, deduped: true, dedupKey };

  try {
    await writeFile(join(dir, filename), JSON.stringify(entry), {
      encoding: "utf-8",
      flag: "wx",
      mode: FILE_MODE,
    });
    return { ok: true, path: filename, dedupKey };
  } catch (error) {
    if (error?.code !== "EEXIST") {
      if (markerCreated) await removeManualMarker(dir, dedupKey);
      return { ok: false, error: error?.message || String(error), dedupKey };
    }
  }
  const duplicate = await findExistingByDedupKey(dir, dedupKey);
  if (duplicate)
    return { ok: true, path: duplicate.filename, deduped: true, dedupKey };
  if (markerCreated) await removeManualMarker(dir, dedupKey);
  return {
    ok: false,
    error: `pending file exists but is unreadable: ${filename}`,
    dedupKey,
  };
}

export async function listPending(scope) {
  const dir = scopeDir(scope);
  let files;
  try {
    await recoverStaleProcessing(dir);
    files = await readdir(dir);
  } catch {
    return [];
  }
  const entries = [];
  for (const filename of files) {
    if (!filename.endsWith(".json")) continue;
    try {
      const entry = await readEntry(dir, filename);
      if (await hasManualMarker(dir, entry.dedupKey))
        entry.provenance = "manual";
      entries.push({ filename, entry });
    } catch {
      // Corrupt entries fail closed and remain available for operator inspection.
    }
  }
  entries.sort((a, b) => (a.entry.createdAt || 0) - (b.entry.createdAt || 0));
  return entries;
}

export async function claimForReplay(scope, filename) {
  const dir = scopeDir(scope);
  if (!filename.endsWith(".json")) return null;
  const claimed = processingFilename(filename);
  try {
    await rename(join(dir, filename), join(dir, claimed));
    return claimed;
  } catch {
    return null;
  }
}

export async function dequeue(scope, filename) {
  const dir = scopeDir(scope);
  let dedupKey = dedupKeyFromFilename(filename);
  if (!dedupKey) {
    try {
      dedupKey = (await readEntry(dir, filename))?.dedupKey || "";
    } catch {
      /* ignore */
    }
  }
  try {
    await unlink(join(dir, filename));
    await removeManualMarker(dir, dedupKey);
    return true;
  } catch {
    return false;
  }
}

export async function incrementRetry(scope, filename, entry) {
  const dir = scopeDir(scope);
  entry.retries = (entry.retries || 0) + 1;
  if (
    entry.retries >
    envInt("OPENVIKING_PENDING_MAX_RETRIES", DEFAULT_MAX_RETRIES)
  ) {
    await unlink(join(dir, filename)).catch(() => {});
    await removeManualMarker(
      dir,
      entry.dedupKey || dedupKeyFromFilename(filename),
    );
    return false;
  }
  const next = retryFilename(filename, entry.retries);
  const temp = `${next}.tmp.${process.pid}.${Date.now()}`;
  try {
    await writeFile(join(dir, temp), JSON.stringify(entry), {
      encoding: "utf-8",
      flag: "wx",
      mode: FILE_MODE,
    });
    await rename(join(dir, temp), join(dir, next));
    await unlink(join(dir, filename)).catch(() => {});
    return true;
  } catch {
    await unlink(join(dir, temp)).catch(() => {});
    return false;
  }
}

export async function cleanStale(scope) {
  const ttlMs =
    envInt("OPENVIKING_PENDING_TTL_DAYS", DEFAULT_TTL_DAYS) *
    24 *
    60 *
    60 *
    1000;
  let cleaned = 0;
  for (const { filename, entry } of await listPending(scope)) {
    if (
      Date.now() - (entry.createdAt || 0) > ttlMs &&
      (await dequeue(scope, filename))
    )
      cleaned++;
  }
  return cleaned;
}

function retryable(result) {
  if (!result || result.ok) return false;
  const status = Number(result.status || 0);
  return !status || status >= 500 || status === 408 || status === 429;
}

export async function replayPending(scope, fetchJSON, log, options = {}) {
  const pending = await listPending(scope);
  if (pending.length === 0)
    return { replayed: 0, failed: 0, skipped: 0, deferred: 0 };
  const limit = envInt(
    "OPENVIKING_PENDING_REPLAY_LIMIT",
    DEFAULT_REPLAY_LIMIT,
    false,
  );
  log("pending-queue", {
    count: pending.length,
    replayLimit: limit,
    action: "replay-start",
  });
  let replayed = 0,
    failed = 0,
    skipped = 0,
    deferred = 0,
    processed = 0;
  for (const { filename, entry } of pending) {
    if (options.shouldReplay && !options.shouldReplay(entry)) {
      deferred++;
      continue;
    }
    if (processed >= limit) {
      deferred++;
      continue;
    }
    if (
      (entry.retries || 0) >=
      envInt("OPENVIKING_PENDING_MAX_RETRIES", DEFAULT_MAX_RETRIES)
    ) {
      await dequeue(scope, filename);
      skipped++;
      continue;
    }
    const claimed = await claimForReplay(scope, filename);
    if (!claimed) {
      skipped++;
      continue;
    }
    processed++;
    let result;
    try {
      const sid = encodeURIComponent(entry.sessionId);
      if (entry.type === "addMessage") {
        result = await fetchJSON(`/api/v1/sessions/${sid}/messages`, {
          method: "POST",
          body: JSON.stringify(entry.payload),
        });
      } else if (entry.type === "commitSession") {
        result = await fetchJSON(`/api/v1/sessions/${sid}/commit`, {
          method: "POST",
          body: JSON.stringify(entry.payload || {}),
        });
      } else {
        await dequeue(scope, claimed);
        skipped++;
        continue;
      }
    } catch {
      result = { ok: false };
    }
    if (result?.ok) {
      await dequeue(scope, claimed);
      replayed++;
    } else if (!retryable(result)) {
      await dequeue(scope, claimed);
      skipped++;
    } else {
      await incrementRetry(scope, claimed, entry);
      failed++;
      if (entry.type === "addMessage") {
        deferred += Math.max(0, pending.length - processed);
        break;
      }
    }
  }
  const cleaned = await cleanStale(scope);
  log("pending-queue", {
    action: "replay-done",
    replayed,
    failed,
    skipped,
    deferred,
    cleaned,
  });
  return { replayed, failed, skipped, deferred };
}
