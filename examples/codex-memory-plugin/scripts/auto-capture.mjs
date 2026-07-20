#!/usr/bin/env node

/**
 * Stop hook for Codex (turn end).
 *
 * Codex passes JSON on stdin including session_id, transcript_path,
 * last_assistant_message. Stop fires per turn — NOT at session end.
 *
 * Strategy:
 *   1. For this codex session_id, derive one long-lived OpenViking session
 *      id (`cx-<codex-session-id>`) and remember it in state.
 *   2. Read transcript_path, parse JSONL rollout, append every new
 *      user/assistant turn since last capture via add_message.
 *   3. If session pending_tokens crosses commitTokenThreshold, commit while
 *      keeping a recent live tail for continuity.
 *
 * PreCompact still commits deterministically before context compaction, and
 * SessionStart still handles orphaned sessions / idle TTL sweep.
 *
 * Stop output schema accepts {} as a no-op.
 *
 * Note: we deliberately do NOT run an idle-TTL sweep here. State-write-on-
 * every-turn already gives us the freshness signal we need; running the
 * sweep once per session start (in session-start-commit.mjs) is the right
 * cadence. See DESIGN.md §5 ("Sweep trigger").
 */

import { readFile } from "node:fs/promises";
import {
  extractCaptureTurns,
} from "./capture-utils.mjs";
import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { loadState, resolveOvSessionId, saveState } from "./session-state.mjs";
import { maybeDetach, readHookStdin } from "./shared/async-writer.mjs";
import { sendSessionMessages } from "./shared/batch-send.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("auto-capture");
let activePeerId = cfg.peerId || "";

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function noop(message) {
  output(message ? { systemMessage: message } : {});
}

function makeHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (cfg.apiKey) {
    headers["Authorization"] = `Bearer ${cfg.apiKey}`;
    headers["X-API-Key"] = cfg.apiKey;
  }
  if (cfg.sendIdentityHeaders && cfg.account) headers["X-OpenViking-Account"] = cfg.account;
  if (cfg.sendIdentityHeaders && cfg.user) headers["X-OpenViking-User"] = cfg.user;
  if (activePeerId) headers["X-OpenViking-Actor-Peer"] = activePeerId;
  return headers;
}

async function fetchJSONRes(path, init = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), cfg.captureTimeoutMs);
  try {
    const res = await fetch(`${cfg.baseUrl}${path}`, { ...init, headers: makeHeaders(), signal: controller.signal });
    const body = await res.json().catch(() => null);
    if (!body) return { ok: false, status: res.status, error: { message: "empty or invalid JSON response" } };
    if (!res.ok || body.status === "error") {
      return { ok: false, status: res.status, error: body.error || body };
    }
    return { ok: true, status: res.status, result: body.result ?? body };
  } catch (err) {
    return { ok: false, status: 0, error: { message: err?.message || String(err) } };
  } finally {
    clearTimeout(timer);
  }
}

async function fetchJSON(path, init = {}) {
  const r = await fetchJSONRes(path, init);
  return r.ok ? (r.result ?? null) : null;
}

// ---------------------------------------------------------------------------
// Transcript parsing (JSONL rollout)
// ---------------------------------------------------------------------------

function parseTranscript(content) {
  try {
    const data = JSON.parse(content);
    if (Array.isArray(data)) return data;
  } catch { /* not a JSON array */ }
  const lines = content.split("\n").filter((l) => l.trim());
  const out = [];
  for (const line of lines) {
    try { out.push(JSON.parse(line)); } catch { /* skip */ }
  }
  return out;
}

function extractTurns(rolloutEntries) {
  return extractCaptureTurns(rolloutEntries, cfg);
}

async function readTranscriptTurns(transcriptPath) {
  if (!transcriptPath) return [];
  try {
    const raw = await readFile(transcriptPath, "utf-8");
    if (!raw.trim()) return [];
    return extractTurns(parseTranscript(raw));
  } catch (err) {
    logError("transcript_read", err);
    return [];
  }
}

function selectStopTurns(state, turns) {
  const limit = cfg.captureMaxTurnsPerStop;
  if (turns.length <= limit) return turns;
  const skipped = turns.length - limit;
  state.capturedTurnCount += skipped;
  log("backlog_trimmed", { newTurns: turns.length, skipped, selected: limit });
  return turns.slice(-limit);
}

async function appendTurns(ovSessionId, turns, state) {
  const payloads = turns.map((turn) => {
    const body = turn.parts?.length
      ? { role: turn.role, parts: turn.parts }
      : { role: turn.role, content: turn.text };
    if (activePeerId) body.peer_id = activePeerId;
    return body;
  });
  const r = await sendSessionMessages(fetchJSONRes, ovSessionId, payloads, {
    onSent: async (n) => {
      state.capturedTurnCount += n;
      await saveState(state);
    },
  });
  return r.sent;
}

async function maybeCommitByThreshold(ovSessionId, added) {
  if (added <= 0) return { committed: false, pendingTokens: 0, commitCount: 0, totalMessageCount: 0 };
  const meta = await fetchJSON(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}`);
  const pendingTokens = Number(meta?.pending_tokens || 0);
  const commitCount = Number(meta?.commit_count || 0);
  const totalMessageCount = Number(meta?.total_message_count || 0);
  log("pending_tokens", {
    ovSessionId,
    pending: pendingTokens,
    threshold: cfg.commitTokenThreshold,
    keepRecentCount: cfg.commitKeepRecentCount,
  });
  if (pendingTokens < cfg.commitTokenThreshold) {
    return { committed: false, pendingTokens, commitCount, totalMessageCount };
  }
  const commit = await fetchJSON(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}/commit`, {
    method: "POST",
    body: JSON.stringify({ keep_recent_count: cfg.commitKeepRecentCount }),
  });
  const committed = Boolean(commit);
  log("commit", { ovSessionId, ok: committed, pending: pendingTokens });
  return {
    committed,
    pendingTokens,
    commitCount: committed ? commitCount + 1 : commitCount,
    totalMessageCount,
  };
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  if (!cfg.autoCapture) {
    log("skip", { stage: "init", reason: "autoCapture disabled" });
    noop();
    return;
  }

  // Async write mode returns a no-op response immediately; worker stdout is
  // intentionally discarded, so appended-count systemMessage is sync-only.
  if (await maybeDetach(cfg, { approve: () => output({}) })) return;

  let input;
  try {
    input = JSON.parse(await readHookStdin());
  } catch {
    log("skip", { stage: "stdin_parse", reason: "invalid input" });
    noop();
    return;
  }

  const sessionId = input.session_id || "unknown";
  const transcriptPath = input.transcript_path || null;
  const state = await loadState(sessionId);
  activePeerId = cfg.peerId || state.workspacePeerId || resolveEffectivePeerId({ cfg, cwd: process.cwd() }).peerId;
  log("start", { sessionId, transcriptPath, hasPeer: Boolean(activePeerId) });

  const health = await fetchJSON("/health");
  if (!health) {
    logError("health_check", "server unreachable or unhealthy");
    noop();
    return;
  }

  const allTurns = await readTranscriptTurns(transcriptPath);

  // Post-compact transcript-shrink defense: codex's /compact may rewrite or
  // truncate transcript_path. If allTurns has fewer entries than we cached,
  // our slice math would underflow and silently drop turns. Reset the
  // counter so the next slice captures everything in the new transcript.
  // See DESIGN.md "Post-compact transcript shrink".
  if (allTurns.length < state.capturedTurnCount) {
    log("transcript_shrink_detected", {
      cached: state.capturedTurnCount,
      observed: allTurns.length,
    });
    state.capturedTurnCount = 0;
  }

  const newTurns = allTurns.slice(state.capturedTurnCount);

  log("transcript_parse", {
    totalTurns: allTurns.length,
    previouslyCaptured: state.capturedTurnCount,
    newTurns: newTurns.length,
  });

  if (cfg.captureMode === "keyword" && newTurns.length > 0 && !hasCaptureKeyword(newTurns)) {
    log("skip", { stage: "capture_mode", reason: "keyword mode without capture trigger" });
    await saveState(state);
    noop();
    return;
  }

  let added = 0;
  let ovSessionId = "";
  let commitInfo = { committed: false, pendingTokens: 0, commitCount: 0, totalMessageCount: 0 };
  if (newTurns.length > 0) {
    ovSessionId = resolveOvSessionId(state);
    if (!ovSessionId) {
      logError("resolve_ov_session", "failed to derive OV session id");
    } else {
      const turnsToAppend = selectStopTurns(state, newTurns);
      await saveState(state);
      added = await appendTurns(ovSessionId, turnsToAppend, state);
      log("appended", { ovSessionId, added });
      commitInfo = await maybeCommitByThreshold(ovSessionId, added);
    }
  }

  await saveState(state);

  // could also sweep here, deliberately not — see header comment + DESIGN.md §5.

  if (added > 0) {
    noop(
      `appended ${added} turn(s) to OpenViking session ${state.ovSessionId}` +
      (commitInfo.committed ? " (committed)" : ""),
    );
  } else {
    noop();
  }
}

function hasCaptureKeyword(turns) {
  return turns.some((turn) => /\b(remember|memorize|store|save|capture|note|record)\b|记住|保存|记录|记忆/i.test(turn.text));
}

main().catch((err) => { logError("uncaught", err); noop(); });
