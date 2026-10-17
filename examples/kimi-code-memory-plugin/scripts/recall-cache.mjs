/**
 * Per-session recall cache. The host can deliver the same prompt twice
 * (retry, resume, duplicate event), and a question repeated within minutes
 * answers from the same memories; neither should pay for another search or
 * inject a second copy.
 *
 * Lives in its own file so the prompt hook never rewrites the capture state
 * that the capture hook owns under its lock.
 */

import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { getStateDir, safeId } from "./session-state.mjs";

export const RECALL_DUPLICATE_WINDOW_MS = 500;
export const RECALL_CACHE_TTL_MS = 10 * 60 * 1000;

/** "duplicate" = same prompt within the double-fire window; "hit" = reuse. */
export function decideRecall(cache, promptHash, now = Date.now()) {
  if (!cache || cache.hash !== promptHash) return "miss";
  const age = now - Number(cache.at || 0);
  if (age < RECALL_DUPLICATE_WINDOW_MS) return "duplicate";
  if (cache.block && age < RECALL_CACHE_TTL_MS) return "hit";
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
  await mkdir(getStateDir(), { recursive: true });
  const path = cachePath(sessionId);
  const staging = `${path}.${process.pid}.tmp`;
  await writeFile(staging, `${JSON.stringify(value)}\n`, "utf-8");
  await rename(staging, path);
}
