/**
 * Per-codex-session state for the OpenViking memory plugin.
 *
 * One state file per codex session_id, holding the long-lived OpenViking
 * session id that we incrementally append turns to via the Stop hook. The
 * OV session id is derived as `cx-<codex-session-id>` for new captures.
 * The OV session is committed (which extracts memories) by the PreCompact
 * hook or by the idle-sweep that runs at the tail of each Stop.
 *
 * State directory: $OPENVIKING_CODEX_STATE_DIR or ~/.openviking/codex-plugin-state
 */

import { randomUUID } from "node:crypto";
import { mkdir, readFile, readdir, readlink, rename, rm, stat, writeFile } from "node:fs/promises";
import { homedir, hostname } from "node:os";
import { basename, join } from "node:path";
import { deriveCodexSessionId } from "./shared/session-model.mjs";

const DEFAULT_STATE_DIR = join(homedir(), ".openviking", "codex-plugin-state");
const DEFAULT_LOCK_TIMEOUT_MS = 60_000;
const DEFAULT_LOCK_RETRY_MS = 25;
const LIST_STATES_LOCK_TIMEOUT_MS = 250;
const AVAILABLE_LOCK_FILE = "available";
const OWNER_LOCK_PREFIX = "owner-";
const CLAIM_LOCK_PREFIX = "claim-";

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

function lockPath(codexSessionId) {
  return `${statePath(codexSessionId)}.lock`;
}

function revisionPath(codexSessionId) {
  return join(lockPath(codexSessionId), "revision");
}

function tempBasename(codexSessionId) {
  return `${basename(statePath(codexSessionId))}.tmp`;
}

function defaultState(codexSessionId, revision = 0) {
  const now = Date.now();
  return {
    codexSessionId,
    ovSessionId: null,
    workspacePeerId: "",
    capturedTurnCount: 0,
    revision,
    createdAt: now,
    lastUpdatedAt: now,
  };
}

function positiveDuration(value, fallback, minimum = 1) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= minimum ? Math.floor(parsed) : fallback;
}

function stateRevision(state) {
  const revision = Number(state?.revision);
  return Number.isSafeInteger(revision) && revision >= 0 ? revision : 0;
}

function lockOptions(options = {}) {
  return {
    lockTimeoutMs: positiveDuration(
      options.lockTimeoutMs ?? process.env.OPENVIKING_CODEX_STATE_LOCK_TIMEOUT_MS,
      DEFAULT_LOCK_TIMEOUT_MS,
    ),
    retryDelayMs: positiveDuration(options.retryDelayMs, DEFAULT_LOCK_RETRY_MS),
  };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function readJsonFile(path) {
  try {
    return JSON.parse(await readFile(path, "utf-8"));
  } catch {
    return null;
  }
}

let machineIdentityPromise;
function currentMachineIdentity() {
  if (!machineIdentityPromise) {
    machineIdentityPromise = (async () => {
      if (process.platform !== "linux") return null;
      for (const path of ["/etc/machine-id", "/var/lib/dbus/machine-id"]) {
        try {
          const value = (await readFile(path, "utf-8")).trim();
          if (value) return `linux:${value}`;
        } catch { /* try the next stable machine-id location */ }
      }
      return null;
    })();
  }
  return machineIdentityPromise;
}

function parseProcStat(raw) {
  const end = raw.lastIndexOf(")");
  const firstSpace = raw.indexOf(" ");
  if (end < 0 || firstSpace < 0) return null;
  const namespacePid = raw.slice(0, firstSpace);
  // After comm, index 0 is field 3; starttime is field 22 (index 19).
  const startTime = raw.slice(end + 1).trim().split(/\s+/)[19];
  return namespacePid && startTime ? { namespacePid, startTime } : null;
}

async function currentPidNamespaceIdentity() {
  if (process.platform !== "linux") return null;
  try {
    return await readlink("/proc/self/ns/pid");
  } catch {
    return null;
  }
}

async function currentProcessStartIdentity() {
  if (process.platform !== "linux") return null;
  try {
    // `/proc/self/stat` reveals the PID in this process's mounted PID namespace.
    // The claim separately records that namespace identity; include Linux's
    // boot id so a reboot cannot make (namespace-pid,start-ticks) look reused.
    const [raw, bootId] = await Promise.all([
      readFile("/proc/self/stat", "utf-8"),
      readFile("/proc/sys/kernel/random/boot_id", "utf-8"),
    ]);
    const parsed = parseProcStat(raw);
    return parsed
      ? `linux:${bootId.trim()}:${parsed.namespacePid}:${parsed.startTime}`
      : null;
  } catch {
    return null;
  }
}

async function recordedProcessIsAlive(identity) {
  const match = /^linux:([^:]+):(\d+):(\d+)$/.exec(String(identity || ""));
  if (!match) return null;
  const [, expectedBootId, namespacePid, expectedStartTime] = match;
  let bootId;
  try {
    bootId = await readFile("/proc/sys/kernel/random/boot_id", "utf-8");
  } catch {
    // Losing access to the boot identity is not proof the owner died.
    return null;
  }
  if (bootId.trim() !== expectedBootId) return false;
  try {
    const raw = await readFile(`/proc/${namespacePid}/stat`, "utf-8");
    return parseProcStat(raw)?.startTime === expectedStartTime;
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    return null;
  }
}

async function ensureLockRoot(codexSessionId) {
  const root = lockPath(codexSessionId);
  const staging = `${root}.init-${process.pid}-${randomUUID()}`;
  let installed = false;
  try {
    // Build a complete lock directory off to the side, then publish it with one
    // directory rename. A crash can leave an unreferenced staging directory but
    // never a canonical lock root missing its baton.
    await mkdir(staging);
    await writeFile(join(staging, AVAILABLE_LOCK_FILE), "", { flag: "wx" });
    await rename(staging, root);
    installed = true;
  } catch (error) {
    if (error?.code !== "EEXIST" && error?.code !== "ENOTEMPTY") throw error;
  } finally {
    if (!installed) await rm(staging, { recursive: true, force: true }).catch(() => {});
  }
  return root;
}

function ownerToken(file) {
  return file.startsWith(OWNER_LOCK_PREFIX) ? file.slice(OWNER_LOCK_PREFIX.length) : "";
}

function claimToken(file) {
  return file.startsWith(CLAIM_LOCK_PREFIX) && file.endsWith(".json")
    ? file.slice(CLAIM_LOCK_PREFIX.length, -".json".length)
    : "";
}

async function claimOwnerIsDefinitelyDead(owner) {
  // Automatic recovery needs identities that have the same meaning to both
  // processes. Hostname/PID alone is insufficient across NFS clients, container
  // PID namespaces, PID reuse, and non-Linux hosts.
  if (process.platform !== "linux" || !owner || owner.hostname !== hostname()) return false;
  const [localMachineIdentity, localPidNamespaceIdentity] = await Promise.all([
    currentMachineIdentity(),
    currentPidNamespaceIdentity(),
  ]);
  if (
    !owner.machineIdentity
    || !localMachineIdentity
    || owner.machineIdentity !== localMachineIdentity
    || !owner.pidNamespaceIdentity
    || !localPidNamespaceIdentity
    || owner.pidNamespaceIdentity !== localPidNamespaceIdentity
  ) return false;
  return await recordedProcessIsAlive(owner.processStartIdentity) === false;
}

async function cleanupDeadUnownedClaims(root, keepToken = "") {
  let files;
  try {
    files = await readdir(root);
  } catch {
    return;
  }
  const ownerTokens = new Set(files.map(ownerToken).filter(Boolean));
  await Promise.all(files.map(async (file) => {
    const token = claimToken(file);
    if (!token || token === keepToken || ownerTokens.has(token)) return;
    const path = join(root, file);
    const owner = await readJsonFile(path);
    if (await claimOwnerIsDefinitelyDead(owner)) {
      // A live contender can have a claim before it owns the baton. Remove only
      // an ownerless claim whose recorded process is positively proven dead;
      // remote, legacy, and non-Linux claims remain fail-safe/manual cleanup.
      await rm(path, { force: true }).catch(() => {});
    }
  }));
}

async function reclaimDeadSameHostOwner(root) {
  let files;
  try {
    files = await readdir(root);
  } catch {
    return false;
  }
  // If the baton is available there is no active owner to reclaim. Any
  // leftover claim metadata is harmless and cleaned by its contender.
  if (files.includes(AVAILABLE_LOCK_FILE)) return false;

  for (const file of files) {
    const token = ownerToken(file);
    if (!token) continue;
    const ownerPath = join(root, file);
    const claimPath = join(root, `${CLAIM_LOCK_PREFIX}${token}.json`);
    const owner = await readJsonFile(claimPath);
    // Never expire a different host's lease from wall-clock age. Without a
    // remote fencing token, timeout-based stealing can overlap a paused but
    // still-live owner. Cross-host crash recovery is therefore explicit/manual.
    if (!owner || owner.hostname !== hostname()) continue;
    const dead = await claimOwnerIsDefinitelyDead(owner);
    // On non-Linux platforms hostname + PID is not a trustworthy host/process
    // identity (hostnames can collide and PIDs can be reused). Without Linux's
    // machine/PID-namespace/start tuple, leave recovery to an operator.
    if (!dead) continue;

    try {
      // The source path is unique to this owner token. If the owner released
      // and another process acquired in between our check and this rename, this
      // exact source is gone; we can never rename/delete the new owner's path.
      await rename(ownerPath, join(root, AVAILABLE_LOCK_FILE));
    } catch (error) {
      if (error?.code === "ENOENT") continue;
      throw error;
    }
    await rm(claimPath, { force: true }).catch(() => {});
    return true;
  }
  return false;
}

async function acquireStateLock(codexSessionId, options = {}) {
  const { lockTimeoutMs, retryDelayMs } = lockOptions(options);
  await mkdir(getStateDir(), { recursive: true });
  const root = await ensureLockRoot(codexSessionId);
  const token = randomUUID();
  const ownerPath = join(root, `${OWNER_LOCK_PREFIX}${token}`);
  const claimPath = join(root, `${CLAIM_LOCK_PREFIX}${token}.json`);
  const deadline = Date.now() + lockTimeoutMs;
  let acquired = false;

  await writeFile(claimPath, JSON.stringify({
    token,
    pid: process.pid,
    hostname: hostname(),
    machineIdentity: await currentMachineIdentity(),
    pidNamespaceIdentity: await currentPidNamespaceIdentity(),
    processStartIdentity: await currentProcessStartIdentity(),
    acquiredAt: Date.now(),
  }), { flag: "wx" });

  try {
    while (true) {
      try {
        await rename(join(root, AVAILABLE_LOCK_FILE), ownerPath);
        acquired = true;
        // A contender killed before acquiring leaves only claim metadata. The
        // current fenced owner can safely prune claims whose recorded Linux
        // process is conclusively dead, without touching live/remote contenders.
        await cleanupDeadUnownedClaims(root, token).catch(() => {});
        break;
      } catch (error) {
        if (error?.code !== "ENOENT") throw error;
      }

      if (await reclaimDeadSameHostOwner(root)) continue;
      if (Date.now() >= deadline) {
        const error = new Error(`Timed out waiting for OpenViking Codex state lock: ${codexSessionId}`);
        error.code = "OPENVIKING_STATE_LOCK_TIMEOUT";
        throw error;
      }
      await sleep(Math.min(retryDelayMs, Math.max(1, deadline - Date.now())));
    }
  } finally {
    if (!acquired) await rm(claimPath, { force: true }).catch(() => {});
  }

  return async () => {
    try {
      // Release moves only our unique source path. If a confirmed-dead-owner
      // recovery already moved it, ENOENT is harmless and cannot affect the
      // current owner, whose source path contains a different token.
      await rename(ownerPath, join(root, AVAILABLE_LOCK_FILE));
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    } finally {
      await rm(claimPath, { force: true }).catch(() => {});
    }
  };
}

function isTempFile(file, codexSessionId) {
  const prefix = tempBasename(codexSessionId);
  return file === prefix || file.startsWith(`${prefix}-`);
}

async function listTempPaths(codexSessionId) {
  try {
    const dir = getStateDir();
    return (await readdir(dir))
      .filter((file) => isTempFile(file, codexSessionId))
      .map((file) => join(dir, file));
  } catch {
    return [];
  }
}

async function readCompleteState(path, codexSessionId) {
  try {
    const raw = await readFile(path, "utf-8");
    const parsed = JSON.parse(raw);
    if (!parsed || parsed.codexSessionId !== codexSessionId) return null;
    const info = await stat(path);
    return {
      path,
      state: { ...defaultState(codexSessionId), ...parsed },
      revision: stateRevision(parsed),
      writtenAt: Number(parsed.lastUpdatedAt) || info.mtimeMs,
      mtimeMs: info.mtimeMs,
    };
  } catch {
    return null;
  }
}

async function removeTempFiles(codexSessionId, except = "") {
  const paths = await listTempPaths(codexSessionId);
  await Promise.all(paths
    .filter((path) => path !== except)
    .map((path) => rm(path, { force: true }).catch(() => {})));
}

async function readPersistedRevision(codexSessionId) {
  try {
    const revision = Number(await readFile(revisionPath(codexSessionId), "utf-8"));
    return Number.isSafeInteger(revision) && revision >= 0 ? revision : 0;
  } catch {
    return 0;
  }
}

async function writePersistedRevision(codexSessionId, revision) {
  const final = revisionPath(codexSessionId);
  const tmp = `${final}.tmp-${process.pid}-${randomUUID()}`;
  let renamed = false;
  try {
    await writeFile(tmp, String(revision));
    await rename(tmp, final);
    renamed = true;
  } finally {
    if (!renamed) await rm(tmp, { force: true }).catch(() => {});
  }
}

async function loadStateUnlocked(codexSessionId) {
  const persistedRevision = await readPersistedRevision(codexSessionId);
  const final = statePath(codexSessionId);
  const persisted = await readCompleteState(final, codexSessionId);
  // A process can die after writeFile completes but before rename, leaving a
  // valid old final beside a newer complete temp. Compare both; do not discard
  // the completed update merely because the old final is still parseable. This
  // also understands the legacy fixed `<id>.json.tmp` name.
  const tempCandidates = (await Promise.all(
    (await listTempPaths(codexSessionId)).map((path) => readCompleteState(path, codexSessionId)),
  )).filter(Boolean);
  const candidates = [persisted, ...tempCandidates]
    .filter(Boolean)
    .sort((a, b) => (b.revision - a.revision)
      || (b.writtenAt - a.writtenAt)
      || (b.mtimeMs - a.mtimeMs));
  const winner = candidates[0];
  if (!winner) {
    await removeTempFiles(codexSessionId);
    return defaultState(codexSessionId, persistedRevision);
  }

  if (winner.revision < persistedRevision) {
    // clearState writes a tombstone revision before removing the final file.
    // If it crashed between those steps, the higher counter proves this
    // otherwise-valid final/temp belongs to the pre-clear generation.
    await removeTempFiles(codexSessionId);
    await rm(final, { force: true });
    return defaultState(codexSessionId, persistedRevision);
  }

  if (winner.path !== final) await rename(winner.path, final);
  await removeTempFiles(codexSessionId);
  if (winner.revision > persistedRevision) {
    // Repair the complementary save crash window: state rename succeeded but
    // the monotonic counter update did not.
    await writePersistedRevision(codexSessionId, winner.revision);
  }
  return winner.state;
}

async function saveStateUnlocked(state, revision = stateRevision(state) + 1) {
  if (!state || !state.codexSessionId) return;
  await mkdir(getStateDir(), { recursive: true });
  const next = { ...state, revision, lastUpdatedAt: Date.now() };
  const final = statePath(state.codexSessionId);
  const tmp = `${final}.tmp-${process.pid}-${randomUUID()}`;
  let renamed = false;
  try {
    await writeFile(tmp, JSON.stringify(next));
    await rename(tmp, final);
    renamed = true;
    await writePersistedRevision(state.codexSessionId, revision);
    Object.assign(state, next);
    return next;
  } finally {
    if (!renamed) await rm(tmp, { force: true }).catch(() => {});
  }
}

async function clearStateUnlocked(codexSessionId, revision) {
  // Persist a tombstone generation before deleting the old final. On restart,
  // loadStateUnlocked ignores any state whose revision predates this counter.
  await writePersistedRevision(codexSessionId, revision);
  // Remove recoverable temps first. If this process is killed during clear,
  // either the old final still exists or all state is gone; an old temp can
  // never be left alone and resurrect a state that was intentionally cleared.
  await removeTempFiles(codexSessionId);
  await rm(statePath(codexSessionId), { force: true });
}

/**
 * Run a complete read/remote-work/write lifecycle under one cross-process,
 * per-session lock. Codex invokes different hooks in different Node processes,
 * so a module-local promise queue is not sufficient.
 */
export async function withStateTransaction(codexSessionId, callback, options = {}) {
  if (!codexSessionId) throw new Error("codexSessionId is required for a state transaction");
  if (typeof callback !== "function") throw new TypeError("state transaction callback must be a function");
  const release = await acquireStateLock(codexSessionId, options);
  try {
    const state = await loadStateUnlocked(codexSessionId);
    let revision = stateRevision(state);
    return await callback({
      state,
      save: async (next = state) => {
        if (!next || next.codexSessionId !== codexSessionId) {
          const error = new Error(`State transaction ${codexSessionId} cannot save another session`);
          error.code = "OPENVIKING_STATE_SESSION_MISMATCH";
          throw error;
        }
        const saved = await saveStateUnlocked(next, Math.max(revision, stateRevision(next)) + 1);
        revision = saved.revision;
        // Keep the transaction's originally-loaded snapshot revision current
        // even when a caller saved a replacement object via `{ ...state }`.
        state.revision = saved.revision;
        state.lastUpdatedAt = saved.lastUpdatedAt;
        return saved;
      },
      clear: async () => {
        revision += 1;
        await clearStateUnlocked(codexSessionId, revision);
        state.revision = revision;
        return revision;
      },
    });
  } finally {
    await release();
  }
}

export async function loadState(codexSessionId) {
  return withStateTransaction(codexSessionId, ({ state }) => state);
}

export async function saveState(state) {
  if (!state || !state.codexSessionId) return;
  return withStateTransaction(state.codexSessionId, ({ state: current, save }) => {
    if (stateRevision(state) !== stateRevision(current)) {
      const error = new Error(`Refusing stale OpenViking Codex state save: ${state.codexSessionId}`);
      error.code = "OPENVIKING_STALE_STATE_SAVE";
      throw error;
    }
    return save(state);
  });
}

export async function clearState(codexSessionId) {
  return withStateTransaction(codexSessionId, ({ clear }) => clear());
}

export async function listStates() {
  try {
    const dir = getStateDir();
    const files = await readdir(dir);
    const sessionIds = new Set();
    for (const file of files) {
      // Scan final files plus recoverable legacy/unique temps. A crash may
      // leave only a complete temp, and SessionStart's orphan sweep must still
      // discover that session rather than silently stranding it forever.
      if (!file.endsWith(".json") && !/\.json\.tmp(?:-|$)/.test(file)) continue;
      try {
        const raw = await readFile(join(dir, file), "utf-8");
        const parsed = JSON.parse(raw);
        if (parsed?.codexSessionId) sessionIds.add(parsed.codexSessionId);
      } catch { /* skip */ }
    }

    const states = await Promise.all([...sessionIds].map(async (codexSessionId) => {
      try {
        return await withStateTransaction(codexSessionId, async ({ state }) => {
          // loadStateUnlocked (called by the transaction) has now selected and
          // recovered the newest complete candidate. Re-check that a final file
          // really exists so a concurrently-cleared candidate does not turn into
          // a phantom default state in the sweep.
          return await readCompleteState(statePath(codexSessionId), codexSessionId)
            ? state
            : null;
        }, { lockTimeoutMs: LIST_STATES_LOCK_TIMEOUT_MS });
      } catch {
        // A busy live hook is safer to omit from this sweep; the next
        // SessionStart will retry after it releases the session lock.
        return null;
      }
    }));
    return states.filter(Boolean);
  } catch {
    return [];
  }
}
