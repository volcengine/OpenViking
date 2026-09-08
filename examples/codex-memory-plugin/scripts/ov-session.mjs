/**
 * Shared OpenViking HTTP + transcript helpers for the Codex capture hooks.
 *
 * Stop (auto-capture), PreCompact and SessionEnd all do the same three things:
 * talk to the OV session API with the plugin's headers, parse the JSONL
 * rollout at `transcript_path`, and append everything past the state cursor.
 * This module owns that logic so the three hooks cannot drift apart.
 *
 * Codex-local on purpose: it depends on Codex rollout shapes and on
 * `session-state.mjs`, so it does not belong under `scripts/shared/`.
 */

import { readFile } from "node:fs/promises";
import { extractCaptureTurns, findLastHumanTurnIndex } from "./capture-utils.mjs";
import { resolveOvSessionId, saveState } from "./session-state.mjs";
import { makeAgentFetchJSON } from "./shared/agent-hook-runtime.mjs";
import { sendSessionMessages } from "./shared/batch-send.mjs";

/**
 * Build the `{ fetchJSONRes, fetchJSON }` pair used by every capture hook.
 * The peer id is read through a getter because callers only know it after
 * loading state (which happens under the session lock).
 */
export function makeFetchJSON(cfg, { getActorPeerId = () => "" } = {}) {
  const { fetchJSON: fetchJSONRes } = makeAgentFetchJSON(cfg, process.cwd(), {
    defaultTimeoutMs: cfg.captureTimeoutMs,
    getActorPeerId,
    // A capture hook would rather retry a body it cannot parse than record the
    // turn as sent.
    requireJsonBody: true,
  });

  async function fetchJSON(path, init = {}) {
    const r = await fetchJSONRes(path, init);
    return r.ok ? (r.result ?? null) : null;
  }

  return { fetchJSONRes, fetchJSON };
}

export function commitOvSession(fetchJSONRes, ovSessionId, body = {}) {
  return fetchJSONRes(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}/commit`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

function parseTranscript(content) {
  try {
    const data = JSON.parse(content);
    if (Array.isArray(data)) return data;
  } catch { /* not a JSON array */ }
  const out = [];
  for (const line of content.split("\n")) {
    if (!line.trim()) continue;
    try { out.push(JSON.parse(line)); } catch { /* skip */ }
  }
  return out;
}

/**
 * Read and normalize the rollout at `transcriptPath`.
 *
 * `ok` distinguishes "read it, it has N turns" from "could not read it".
 * Callers must not treat `ok: false` as a transcript shrink — resetting the
 * cursor on a transient read error replays the whole session.
 */
export async function readTranscriptTurns(transcriptPath, cfg, logError) {
  if (!transcriptPath) return { turns: [], ok: false };
  try {
    const raw = await readFile(transcriptPath, "utf-8");
    if (!raw.trim()) return { turns: [], ok: true };
    return { turns: extractCaptureTurns(parseTranscript(raw), cfg), ok: true };
  } catch (err) {
    logError?.("transcript_read", err);
    return { turns: [], ok: false };
  }
}

export function hasCaptureKeyword(turns) {
  return turns.some((turn) =>
    /\b(remember|memorize|store|save|capture|note|record)\b|记住|保存|记录|记忆/i.test(turn.text)
  );
}

/**
 * Append every transcript turn past `state.capturedTurnCount`.
 *
 * Advances the cursor (and persists it) after each durably sent batch, so a
 * failure halfway through never replays what already landed. Mutates `state`;
 * the caller owns the session lock.
 *
 * `shouldSend(newTurns)` lets a caller veto the send (keyword capture mode)
 * without disturbing the cursor.
 *
 * Returns `{ newTurns, added, ovSessionId, skipped, unreadable }`. `unreadable`
 * separates "the transcript is empty" from "the transcript could not be read":
 * committing on the latter would archive a session whose tail turns were never
 * seen, so callers must keep the session live instead.
 */
export async function catchUpTurns({
  state,
  transcriptPath,
  fetchJSONRes,
  activePeerId = "",
  cfg,
  log,
  logError,
  heartbeat,
  shouldSend,
}) {
  // Remember the rollout path so the SessionStart sweep can catch up turns for
  // a session whose Stop/SessionEnd workers never ran.
  if (transcriptPath) state.transcriptPath = transcriptPath;

  const { turns, ok } = await readTranscriptTurns(transcriptPath, cfg, logError);

  if (!ok || turns.length === 0) {
    log?.("transcript_empty", {
      readable: ok,
      previouslyCaptured: state.capturedTurnCount,
    });
    return { newTurns: [], added: 0, ovSessionId: "", unreadable: Boolean(transcriptPath) && !ok };
  }

  // Post-compact transcript-shrink defense: codex's /compact may rewrite or
  // truncate transcript_path. Resume at the latest human turn so the current
  // interaction is captured without replaying compacted history.
  if (turns.length < state.capturedTurnCount) {
    const humanIndex = findLastHumanTurnIndex(turns);
    log?.("transcript_shrink_detected", {
      cached: state.capturedTurnCount,
      observed: turns.length,
      resumeFrom: Math.max(0, humanIndex),
      fallback: humanIndex < 0 ? "full_transcript" : undefined,
    });
    state.capturedTurnCount = Math.max(0, humanIndex);
  }

  const newTurns = turns.slice(state.capturedTurnCount);
  log?.("transcript_parse", {
    totalTurns: turns.length,
    previouslyCaptured: state.capturedTurnCount,
    newTurns: newTurns.length,
  });
  if (newTurns.length === 0) return { newTurns, added: 0, ovSessionId: "" };

  if (shouldSend && !shouldSend(newTurns)) {
    log?.("skip", { stage: "capture_mode", reason: "keyword mode without capture trigger" });
    return { newTurns, added: 0, ovSessionId: "", skipped: "capture_mode" };
  }

  // Only revive the OV session id when there is something to send; otherwise
  // an already-committed session would look live again on every hook run.
  const ovSessionId = resolveOvSessionId(state);
  if (!ovSessionId) {
    logError?.("resolve_ov_session", "failed to derive OV session id");
    return { newTurns, added: 0, ovSessionId: "" };
  }

  const payloads = newTurns.map((turn) => {
    const body = turn.parts?.length
      ? { role: turn.role, parts: turn.parts }
      : { role: turn.role, content: turn.text };
    if (activePeerId) body.peer_id = activePeerId;
    return body;
  });

  const r = await sendSessionMessages(fetchJSONRes, ovSessionId, payloads, {
    // Codex hooks are short-lived subprocesses with no retry of their own, so
    // a retryable failure has to survive on disk until the next SessionStart
    // replays it. Without this the turns were simply dropped.
    enqueueOnRetryable: true,
    onSent: async (n) => {
      state.capturedTurnCount += n;
      await saveState(state);
      await heartbeat?.();
    },
  });
  return { newTurns, added: r.sent, ovSessionId };
}
