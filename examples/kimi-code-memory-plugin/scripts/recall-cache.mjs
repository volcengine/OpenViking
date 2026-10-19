/**
 * Per-session recall cache. The host can deliver the same prompt twice
 * (retry, resume, duplicate event), and a question repeated within minutes
 * answers from the same memories; neither should pay for another search or
 * inject a second copy.
 *
 * Lives in its own file so the prompt hook never rewrites the capture state
 * that the capture hook owns under its lock.
 */

import { mkdir, readdir, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { getStateDir, safeId } from "./session-state.mjs";

export const RECALL_DUPLICATE_WINDOW_MS = 500;
export const RECALL_CACHE_TTL_MS = 10 * 60 * 1000;
// A claim is a promise to answer: if its writer dies the prompt must still be
// able to recall, so the claim expires on its own.
export const RECALL_CLAIM_TTL_MS = 30 * 1000;
const RECALL_CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000;

/** "duplicate" = same prompt in flight or double-fired; "hit" = reuse. */
export function decideRecall(cache, promptHash, now = Date.now()) {
  if (!cache || cache.hash !== promptHash) return "miss";
  const age = now - Number(cache.at || 0);
  if (age < RECALL_DUPLICATE_WINDOW_MS) return "duplicate";
  // A second event for the same prompt arrives while the first is still
  // searching; `at` is written before the search now, so this catches it.
  if (cache.pending && age < RECALL_CLAIM_TTL_MS) return "duplicate";
  if ((cache.block || cache.empty) && age < RECALL_CACHE_TTL_MS) return "hit";
  return "miss";
}

function cachePath(sessionId) {
  return join(getStateDir(), `${safeId(sessionId)}.recall.json`);
}

export async function readRecallCache(sessionId) {
  try {
    return JSON.parse(await readFile(cachePath(sessionId), "utf-8"));
  } catch {
    return null;
  }
}

export async function writeRecallCache(sessionId, value) {
  await mkdir(getStateDir(), { recursive: true, mode: 0o700 });
  const path = cachePath(sessionId);
  const staging = `${path}.${process.pid}.tmp`;
  // The block is recalled memory: same sensitivity as the pending queue, which
  // keeps its files 0600.
  await writeFile(staging, `${JSON.stringify(value)}\n`, { encoding: "utf-8", mode: 0o600 });
  await rename(staging, path);
  await pruneOldCaches();
}

/** Claim the prompt before searching, so an overlapping event backs off. */
export async function claimRecall(sessionId, promptHash, now = Date.now()) {
  await writeRecallCache(sessionId, { hash: promptHash, at: now, block: "", pending: true });
}

/** Drop caches nothing has touched for a day; one file accumulates per session. */
async function pruneOldCaches(now = Date.now()) {
  const dir = getStateDir();
  let names;
  try {
    names = await readdir(dir);
  } catch {
    return;
  }
  for (const name of names) {
    if (!name.endsWith(".recall.json")) continue;
    const path = join(dir, name);
    try {
      if (now - (await stat(path)).mtimeMs > RECALL_CACHE_MAX_AGE_MS) await rm(path, { force: true });
    } catch {}
  }
}
