/**
 * Per-Kimi-session state for the OpenViking memory plugin.
 *
 * One state file per Kimi session_id, holding the long-lived OpenViking
 * session id that capture incrementally appends messages to. The OV session
 * id is derived as `kc-<kimi-session-id>` (shared/session-model.mjs
 * deriveHarnessSessionId; the `kc-` prefix is kept from the previous
 * agent-hook-plugin adapter for data compatibility).
 *
 * Cursor model (DESIGN.md §5.5): a turnId acknowledgement set
 * (`capturedTurnIds`, dedupKey `turnId:role` or the tool call id) rather than
 * an index — wire.jsonl may be truncated by repairWireJournal, and an index
 * cursor would drift while the ack set does not.
 *
 * Sidecars next to `<safeId>.json`:
 *   - `<safeId>.lock` — exclusive mkdir lock serializing writers (see
 *     withSessionLock).
 *
 * State directory: $OPENVIKING_KIMICODE_STATE_DIR or ~/.openviking/kimicode-plugin-state
 */

import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { deriveHarnessSessionId } from "./shared/session-model.mjs";

const DEFAULT_STATE_DIR = join(homedir(), ".openviking", "kimicode-plugin-state");
const LOCK_OWNER_FILE = "owner";

// Same acquisition contract as the shared withAgentHookLock
// (memory-plugin-shared/lib/agent-hook-runtime.mjs): wait briefly, recover
// stale holders, and report `null` instead of running unserialized when the
// lock never comes — two concurrent hook processes must never share the
// critical section, and must never delete a lock they did not take.
const LOCK_WAIT_MS = 5000;
const LOCK_STALE_MS = 60_000;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function getStateDir() {
  return process.env.OPENVIKING_KIMICODE_STATE_DIR || DEFAULT_STATE_DIR;
}

export function safeId(sessionId) {
  return String(sessionId || "unknown").replace(/[^a-zA-Z0-9_-]/g, "_");
}

export function deriveOvSessionId(kimiSessionId) {
  return deriveHarnessSessionId("kc-", String(kimiSessionId || ""));
}

async function statePath(sessionId) {
  const dir = getStateDir();
  await mkdir(dir, { recursive: true, mode: 0o700 });
  return join(dir, `${safeId(sessionId)}.json`);
}

export async function loadState(sessionId, onError = () => {}) {
  const empty = { sessionId, capturedTurnIds: [], capturedSinceCommit: 0, lastTurnId: null };
  let raw;
  try {
    raw = await readFile(await statePath(sessionId), "utf-8");
  } catch (error) {
    // "no state yet" is normal; anything else means the cursor could not be
    // read, and silently resetting it would re-send the whole session.
    if (error?.code !== "ENOENT") onError(`state_unreadable code=${error?.code || "unknown"}`);
    return empty;
  }
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("not an object");
    return parsed;
  } catch (error) {
    onError(`state_corrupt: ${error?.message || String(error)}`);
    return empty;
  }
}

export async function saveState(sessionId, value) {
  const path = await statePath(sessionId);
  const staging = `${path}.${process.pid}.${Date.now()}.tmp`;
  await writeFile(staging, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf-8", mode: 0o600 });
  // Windows refuses the rename while another process holds the destination — a
  // scanner, the doctor reading it, a second hook. Retry briefly rather than
  // dropping acks the server already confirmed.
  let lastError;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await rename(staging, path);
      return;
    } catch (error) {
      lastError = error;
      if (!["EPERM", "EBUSY", "EACCES"].includes(error?.code)) break;
      await sleep(20 * (attempt + 1));
    }
  }
  await rm(staging, { force: true }).catch(() => {});
  throw lastError;
}

/**
 * Serialize writers for one session. Returns the callback's result, or `null`
 * when the lock could not be acquired within LOCK_WAIT_MS (the caller must
 * treat that as "skip this hook", not as success — the next event retries).
 * A holder that died without releasing is reclaimed after LOCK_STALE_MS.
 */
export async function withSessionLock(sessionId, callback) {
  const dir = join(getStateDir(), `${safeId(sessionId)}.lock`);
  // The lock can be the first thing a fresh install touches: auto-recall
  // creates the state directory through loadState, but a short first prompt,
  // or auto-recall disabled, reaches capture before that ever runs.
  await mkdir(getStateDir(), { recursive: true });
  const deadline = Date.now() + LOCK_WAIT_MS;
  const token = `${process.pid}-${randomUUID()}`;
  let acquired = false;
  while (true) {
    try {
      await mkdir(dir, { recursive: false });
      acquired = true;
      break;
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
      let reclaimed = false;
      try {
        if (Date.now() - (await stat(dir)).mtimeMs > LOCK_STALE_MS) {
          reclaimed = await reclaimStaleLock(dir, token);
        }
      } catch {}
      if (reclaimed) continue;
      if (Date.now() >= deadline) return null;
      await sleep(50);
    }
  }
  try {
    // A stale reclaim can hand this directory to a new owner while the old
    // holder is still inside its callback. The token is what lets that holder
    // tell "my lock" from "someone else's" when it finally releases.
    await writeFile(join(dir, LOCK_OWNER_FILE), token, { flag: "wx" });
    return await callback();
  } finally {
    if (acquired) await releaseOwnedLock(dir, token);
  }
}

/**
 * Take a stale lock out of the way without letting two waiters both wipe the
 * same fresh lock: only one process can win the rename, and the loser leaves
 * whatever the winner created alone.
 */
async function reclaimStaleLock(dir, token) {
  const retired = `${dir}.stale-${token}`;
  try {
    await rename(dir, retired);
  } catch {
    return false;
  }
  await rm(retired, { recursive: true, force: true }).catch(() => {});
  return true;
}

/** Remove the lock only when this process still owns it. */
async function releaseOwnedLock(dir, token) {
  try {
    if ((await readFile(join(dir, LOCK_OWNER_FILE), "utf-8")).trim() !== token) return false;
  } catch {
    return false;
  }
  try {
    await rm(dir, { recursive: true, force: true });
    return true;
  } catch {
    return false;
  }
}

function profileMarkerPath(sessionId) {
  return join(getStateDir(), `${safeId(sessionId)}.profile.json`);
}

/**
 * Whether this session already received the one-time profile block. It lives
 * in its own file because the prompt hook must not rewrite the capture state
 * that the capture hook owns under its lock.
 */
export async function profileInjected(sessionId) {
  try {
    await stat(profileMarkerPath(sessionId));
    return true;
  } catch {
    return false;
  }
}

/** Claim the one-time profile injection; false when another hook claimed it. */
export async function markProfileInjected(sessionId) {
  await mkdir(getStateDir(), { recursive: true, mode: 0o700 });
  try {
    await writeFile(profileMarkerPath(sessionId), `${JSON.stringify({ at: Date.now() })}\n`, {
      flag: "wx",
      mode: 0o600,
    });
    return true;
  } catch (error) {
    if (error?.code === "EEXIST") return false;
    throw error;
  }
}
