/**
 * Persistent state management for context engine.
 * Tracks OV session ID, captured turn count, compact timing.
 * State persisted in $TMPDIR/openviking-cc-context-state/<safe-session-id>.json
 */

import { readFile, writeFile, mkdir, rename } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createHash } from "node:crypto";

const STATE_DIR = join(tmpdir(), "openviking-cc-context-state");

function safeId(sessionId) {
  return sessionId.replace(/[^a-zA-Z0-9_-]/g, "_");
}

function stateFilePath(sessionId) {
  return join(STATE_DIR, `${safeId(sessionId)}.json`);
}

/** Derive a deterministic OV session ID from Claude Code session_id. */
export function deriveOvSessionId(ccSessionId) {
  const trimmed = (ccSessionId || "unknown").trim();
  // If already a UUID, use as-is
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(trimmed)) {
    return trimmed.toLowerCase();
  }
  return createHash("sha256").update(`cc-session:${trimmed}`).digest("hex");
}

export async function loadState(sessionId) {
  try {
    const data = await readFile(stateFilePath(sessionId), "utf-8");
    return JSON.parse(data);
  } catch {
    return {
      ovSessionId: deriveOvSessionId(sessionId),
      capturedTurnCount: 0,
      totalTokensAdded: 0,
      turnsSinceCommit: 0,
      lastCommitAt: 0,
      commitCount: 0,
      createdAt: Date.now(),
    };
  }
}

export async function saveState(sessionId, state) {
  try {
    await mkdir(STATE_DIR, { recursive: true });
    const path = stateFilePath(sessionId);
    const tmpPath = path + ".tmp";
    await writeFile(tmpPath, JSON.stringify(state));
    await rename(tmpPath, path);
  } catch { /* best effort */ }
}
