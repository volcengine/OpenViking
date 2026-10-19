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

import { mkdir, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { withDirLock } from "./shared/agent-hook-runtime.mjs";
import { deriveHarnessSessionId } from "./shared/session-model.mjs";

const DEFAULT_STATE_DIR = join(homedir(), ".openviking", "kimicode-plugin-state");

// The lock's wait bound. The acquisition algorithm, the owner stamp and the
// stale takeover are the shared withDirLock's — this plugin only owns where its
// lock lives, because its state layout is its own.
const LOCK_WAIT_MS = 5000;

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
 * A holder that died without releasing is reclaimed after the shared lock's
 * stale window; the algorithm, the owner stamp and the takeover all live in
 * withDirLock so this plugin cannot drift from the other hosts.
 */
export async function withSessionLock(sessionId, callback, { waitMs = LOCK_WAIT_MS } = {}) {
  // The lock can be the first thing a fresh install touches: auto-recall
  // creates the state directory through loadState, but a short first prompt,
  // or auto-recall disabled, reaches capture before that ever runs.
  await mkdir(getStateDir(), { recursive: true });
  return withDirLock(join(getStateDir(), `${safeId(sessionId)}.lock`), callback, {
    waitMs: Math.max(0, Math.min(Number(waitMs) || 0, LOCK_WAIT_MS)),
  });
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
