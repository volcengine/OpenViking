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
 *   - `<safeId>.ended.<ts>` — written by the SessionEnd parent, lock-free, so
 *     the SessionStart sweep can still commit a session whose worker died.
 *   - `<safeId>.lock` — exclusive mkdir lock serializing writers.
 *
 * State directory: $OPENVIKING_KIMICODE_STATE_DIR or ~/.openviking/kimicode-plugin-state
 *
 * SCAFFOLD: structure mirrors examples/codex-memory-plugin/scripts/session-state.mjs;
 * cursor semantics per DESIGN.md §5.5 (ack set, not index).
 */

import { mkdir, readFile, rename, rm, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { deriveHarnessSessionId } from "./shared/session-model.mjs";

const DEFAULT_STATE_DIR = join(homedir(), ".openviking", "kimicode-plugin-state");

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

export async function withSessionLock(sessionId, callback) {
  const dir = join(getStateDir(), `${safeId(sessionId)}.lock`);
  await mkdir(dir, { recursive: false }).catch(() => {});
  try {
    return await callback();
  } finally {
    await rm(dir, { recursive: true, force: true }).catch(() => {});
  }
}
