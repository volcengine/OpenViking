/**
 * Shared runtime state files under ~/.openviking/state/.
 *
 * Hooks (auto-recall, auto-capture) write small JSON snapshots here so the
 * statusline script can render up-to-date info without making network calls
 * for things only the hook knows (last recall stats, pending capture count).
 *
 * Atomic write: temp file + rename, so the statusline never reads a half-
 * written file. Stale entries (older than maxAgeMs) read as null so the
 * statusline degrades to "idle" instead of showing day-old data.
 */

import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { dirname, join } from "node:path";

const OV_HOME = process.env.OPENVIKING_HOME && process.env.OPENVIKING_HOME.trim()
  ? process.env.OPENVIKING_HOME.replace(/^~(?=$|\/)/, homedir())
  : join(homedir(), ".openviking");
export const STATE_DIR = join(OV_HOME, "state");

function ensureDir() {
  try {
    mkdirSync(STATE_DIR, { recursive: true });
  } catch { /* best effort — caller will see write failure */ }
}

export function statePath(name) {
  return join(STATE_DIR, name);
}

export function writeJsonState(name, payload) {
  ensureDir();
  const target = statePath(name);
  const tmp = `${target}.${process.pid}.tmp`;
  try {
    writeFileSync(tmp, JSON.stringify({ ...payload, ts: payload?.ts ?? Date.now() }));
    renameSync(tmp, target);
  } catch { /* best effort: statusline tolerates missing files */ }
}

export function readJsonState(name, { maxAgeMs } = {}) {
  let raw;
  try {
    raw = readFileSync(statePath(name), "utf-8");
  } catch {
    return null;
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (maxAgeMs && typeof parsed?.ts === "number" && Date.now() - parsed.ts > maxAgeMs) {
    return null;
  }
  return parsed;
}

// Per-session capture cursors, `kind` being "capture" or "subagent". A cursor
// lost to a temp-dir purge would resend the whole transcript, so they live
// here; a cursor still in the temp dir from an older release is read until the
// first write replaces it.
function cursorName(id) {
  return `${String(id).replace(/[^a-zA-Z0-9_-]/g, "_")}.json`;
}

export function cursorPath(kind, id) {
  return join(STATE_DIR, `cc-${kind}-state`, cursorName(id));
}

function legacyCursorPath(kind, id) {
  return join(tmpdir(), `openviking-cc-${kind}-state`, cursorName(id));
}

export async function readCursor(kind, id) {
  for (const path of [cursorPath(kind, id), legacyCursorPath(kind, id)]) {
    let raw;
    try {
      raw = await readFile(path, "utf-8");
    } catch (err) {
      if (err?.code === "ENOENT") continue;
      return null;
    }
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }
  return null;
}

export async function writeCursor(kind, id, value) {
  const target = cursorPath(kind, id);
  await mkdir(dirname(target), { recursive: true });
  const tmp = `${target}.${process.pid}.tmp`;
  await writeFile(tmp, JSON.stringify(value));
  await rename(tmp, target);
  await unlink(legacyCursorPath(kind, id)).catch(() => {});
}

export async function removeCursor(kind, id) {
  await Promise.all([cursorPath(kind, id), legacyCursorPath(kind, id)]
    .map((path) => unlink(path).catch(() => {})));
}
