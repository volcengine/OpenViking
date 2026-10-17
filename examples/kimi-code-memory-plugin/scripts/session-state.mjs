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
  await mkdir(dir, { recursive: true });
  return join(dir, `${safeId(sessionId)}.json`);
}

export async function loadState(sessionId) {
  try {
    return JSON.parse(await readFile(await statePath(sessionId), "utf-8"));
  } catch {
    return { sessionId, capturedTurnIds: [], capturedSinceCommit: 0, lastTurnId: null };
  }
}

export async function saveState(sessionId, value) {
  const path = await statePath(sessionId);
  const staging = `${path}.${process.pid}.tmp`;
  await writeFile(staging, `${JSON.stringify(value, null, 2)}\n`, "utf-8");
  await rename(staging, path);
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
      try {
        if (Date.now() - (await stat(dir)).mtimeMs > LOCK_STALE_MS) {
          await rm(dir, { recursive: true, force: true });
          continue;
        }
      } catch {}
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
