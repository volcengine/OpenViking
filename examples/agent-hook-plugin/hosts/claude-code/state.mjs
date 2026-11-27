/**
 * Small JSON snapshots under `<OPENVIKING_HOME>/state/` that the statusline,
 * `/ov` and the MCP proxy read. Written atomically (temp file + rename); a
 * snapshot older than `maxAgeMs` reads as null.
 */

import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export const OV_HOME = process.env.OPENVIKING_HOME && process.env.OPENVIKING_HOME.trim()
  ? process.env.OPENVIKING_HOME.replace(/^~(?=$|\/)/, homedir())
  : join(homedir(), ".openviking");
export const STATE_DIR = join(OV_HOME, "state");

export function statePath(name) {
  return join(STATE_DIR, name);
}

export function writeJsonState(name, payload) {
  try {
    mkdirSync(STATE_DIR, { recursive: true });
  } catch { /* the write below fails instead */ }
  const target = statePath(name);
  const tmp = `${target}.${process.pid}.tmp`;
  try {
    writeFileSync(tmp, JSON.stringify({ ...payload, ts: payload?.ts ?? Date.now() }));
    renameSync(tmp, target);
  } catch { /* best effort */ }
}

export function readJsonState(name, { maxAgeMs } = {}) {
  let parsed;
  try {
    parsed = JSON.parse(readFileSync(statePath(name), "utf-8"));
  } catch {
    return null;
  }
  if (maxAgeMs && typeof parsed?.ts === "number" && Date.now() - parsed.ts > maxAgeMs) return null;
  return parsed;
}
