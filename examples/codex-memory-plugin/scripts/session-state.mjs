/**
 * Per-codex-session state for the OpenViking memory plugin.
 *
 * One state file per codex session_id, holding the long-lived OpenViking
 * session that we incrementally append turns to via the Stop hook. The
 * OV session is committed (which extracts memories) by the PreCompact
 * hook or by the idle-sweep that runs at the tail of each Stop.
 *
 * State directory: $OPENVIKING_CODEX_STATE_DIR or ~/.openviking/codex-plugin-state
 */

import { mkdir, readFile, readdir, rename, rm, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

const DEFAULT_STATE_DIR = join(homedir(), ".openviking", "codex-plugin-state");

export function getStateDir() {
  return process.env.OPENVIKING_CODEX_STATE_DIR || DEFAULT_STATE_DIR;
}

function safeId(codexSessionId) {
  return String(codexSessionId).replace(/[^a-zA-Z0-9_-]/g, "_");
}

function statePath(codexSessionId) {
  return join(getStateDir(), `${safeId(codexSessionId)}.json`);
}

function defaultState(codexSessionId) {
  const now = Date.now();
  return {
    codexSessionId,
    ovSessionId: null,
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

export async function saveState(state) {
  if (!state || !state.codexSessionId) return;
  await mkdir(getStateDir(), { recursive: true });
  const next = { ...state, lastUpdatedAt: Date.now() };
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
        if (parsed?.codexSessionId) out.push(parsed);
      } catch { /* skip */ }
    }
    return out;
  } catch {
    return [];
  }
}
