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

import { createHash } from "node:crypto";
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

function scopeSuffix(stateScope) {
  const scope = String(stateScope || "").trim();
  if (!scope) return "";
  return createHash("sha256").update(scope).digest("hex").slice(0, 16);
}

export function deriveOvSessionId(codexSessionId) {
  return `cx-${safeId(codexSessionId || "unknown")}`;
}

export function rotateOvSessionId(state, blockedSession = null) {
  if (!state) return null;
  const oldOvSessionId = state.ovSessionId;
  if (oldOvSessionId && blockedSession) {
    const blocked = Array.isArray(state.blockedOvSessions) ? state.blockedOvSessions : [];
    state.blockedOvSessions = [
      ...blocked,
      {
        ovSessionId: oldOvSessionId,
        blockedAt: Date.now(),
        ...blockedSession,
      },
    ].slice(-20);
  }
  state.ovSessionId = `${deriveOvSessionId(state.codexSessionId)}-part-${Date.now().toString(36)}`;
  return state.ovSessionId;
}

export function resolveOvSessionId(state) {
  // Keep legacy persisted UUIDs so already-captured turns are not orphaned
  // before their next commit. Fresh or cleared state derives the cx-* id.
  if (state.ovSessionId) return state.ovSessionId;
  state.ovSessionId = deriveOvSessionId(state.codexSessionId);
  return state.ovSessionId;
}

function statePath(codexSessionId, stateScope = "") {
  const suffix = scopeSuffix(stateScope);
  const name = suffix ? `${safeId(codexSessionId)}.${suffix}.json` : `${safeId(codexSessionId)}.json`;
  return join(getStateDir(), name);
}

function defaultState(codexSessionId, stateScope = "") {
  const now = Date.now();
  return {
    codexSessionId,
    stateScope,
    ovSessionId: null,
    blockedOvSessions: [],
    capturedTurnCount: 0,
    createdAt: now,
    lastUpdatedAt: now,
  };
}

export async function loadState(codexSessionId, stateScope = "") {
  try {
    const raw = await readFile(statePath(codexSessionId, stateScope), "utf-8");
    const parsed = JSON.parse(raw);
    return { ...defaultState(codexSessionId, stateScope), ...parsed, stateScope };
  } catch {
    return defaultState(codexSessionId, stateScope);
  }
}

export async function saveState(state, stateScope = state?.stateScope || "") {
  if (!state || !state.codexSessionId) return;
  await mkdir(getStateDir(), { recursive: true });
  const next = { ...state, stateScope, lastUpdatedAt: Date.now() };
  // Atomic write (tmpfile + rename) so a crash mid-write can't leave a
  // truncated/corrupt state file. See DESIGN.md "State file schema".
  const final = statePath(state.codexSessionId, stateScope);
  const tmp = `${final}.tmp`;
  await writeFile(tmp, JSON.stringify(next));
  await rename(tmp, final);
}

export async function clearState(codexSessionId, stateScope = "") {
  try {
    await rm(statePath(codexSessionId, stateScope), { force: true });
  } catch { /* best effort */ }
}

export async function listStates(stateScope = "") {
  try {
    const dir = getStateDir();
    const suffix = scopeSuffix(stateScope);
    const files = await readdir(dir);
    const out = [];
    for (const file of files) {
      // .json only — atomic writes briefly create `<id>.json.tmp`, skipped
      // by this check (endsWith(".json") is false for ".json.tmp").
      if (!file.endsWith(".json")) continue;
      if (suffix && !file.endsWith(`.${suffix}.json`)) continue;
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
