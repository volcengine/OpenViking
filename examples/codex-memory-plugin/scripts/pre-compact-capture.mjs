#!/usr/bin/env node

/**
 * PreCompact hook for Codex.
 *
 * Codex is about to summarize/compact the conversation, after which it will
 * rewrite/truncate `transcript_path`. We need every pre-compact turn to land
 * in OpenViking and the OV session to be committed (so the extractor runs on
 * the full pre-compact transcript).
 *
 * Inline behavior:
 *   1. Append every pending turn to the live OV session in batches via
 *      `/messages/batch` (atomic per chunk, capped by `captureBatchSize`).
 *   2. Inline `POST /commit`. The server's commit Phase 1 (snapshot + clear
 *      live + write archive) runs synchronously before HTTP 200; Phase 2
 *      (memory extraction) runs in the background. So HTTP 200 is the
 *      "live cleared, archive ready" fence we need — no rotate, no
 *      detached worker.
 *   3. On 200, clear `state.ovSessionId`. The post-compact Stop re-derives
 *      the same `cx-<codex-session-id>` and starts a new live session on
 *      the server (POST /messages auto-creates).
 *
 * Inline-path failure: state preserved (incl. ovSessionId) and
 * lastUpdatedAt bumped via saveState; the next PreCompact or
 * SessionStart sweep retries.
 *
 * PreCompact output schema accepts {} as a no-op.
 */

import { readFile } from "node:fs/promises";
import {
  extractTextFromPayload,
  isAssistantSideCaptureRole,
  normalizeCaptureRole,
  shouldCaptureText,
} from "./capture-utils.mjs";
import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { loadState, resolveOvSessionId, saveState } from "./session-state.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("pre-compact");

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function noop(message) {
  output(message ? { systemMessage: message } : {});
}

async function fetchJSON(path, init = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), cfg.captureTimeoutMs);
  try {
    const headers = { "Content-Type": "application/json" };
    if (cfg.apiKey) {
      headers["Authorization"] = `Bearer ${cfg.apiKey}`;
      headers["X-API-Key"] = cfg.apiKey;
    }
    if (cfg.sendIdentityHeaders && cfg.account) headers["X-OpenViking-Account"] = cfg.account;
    if (cfg.sendIdentityHeaders && cfg.user) headers["X-OpenViking-User"] = cfg.user;
    if (cfg.peerId) headers["X-OpenViking-Actor-Peer"] = cfg.peerId;
    const res = await fetch(`${cfg.baseUrl}${path}`, { ...init, headers, signal: controller.signal });
    const body = await res.json().catch(() => null);
    if (!body) return null;
    if (!res.ok || body.status === "error") return null;
    return body.result ?? body;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function appendTurnsBatch(ovSessionId, turns, state) {
  let appended = 0;
  for (let i = 0; i < turns.length; i += cfg.captureBatchSize) {
    const chunk = turns.slice(i, i + cfg.captureBatchSize);
    const messages = chunk.map((turn) => {
      const msg = { role: turn.role, content: turn.text };
      if (cfg.peerId) msg.peer_id = cfg.peerId;
      return msg;
    });
    const result = await fetchJSON(
      `/api/v1/sessions/${encodeURIComponent(ovSessionId)}/messages/batch`,
      { method: "POST", body: JSON.stringify({ messages }) },
    );
    if (!result) return { appended, ok: false };
    appended += chunk.length;
    state.capturedTurnCount += chunk.length;
    await saveState(state);
  }
  return { appended, ok: true };
}

async function commitOvSession(ovSessionId) {
  if (!ovSessionId) return null;
  return fetchJSON(
    `/api/v1/sessions/${encodeURIComponent(ovSessionId)}/commit`,
    { method: "POST", body: JSON.stringify({}) },
  );
}

function parseTranscript(content) {
  try {
    const data = JSON.parse(content);
    if (Array.isArray(data)) return data;
  } catch { /* not array */ }
  const lines = content.split("\n").filter((l) => l.trim());
  const out = [];
  for (const line of lines) {
    try { out.push(JSON.parse(line)); } catch { /* skip */ }
  }
  return out;
}

function extractTurns(entries) {
  const turns = [];
  for (const entry of entries) {
    if (!entry || typeof entry !== "object") continue;
    const payload = entry.payload && typeof entry.payload === "object" ? entry.payload : entry;
    const message = payload.message && typeof payload.message === "object" ? payload.message : null;
    const rawRole = message?.role || payload.role || payload.type || payload.kind;
    const role = normalizeCaptureRole(rawRole);
    if (!role) continue;
    if (isAssistantSideCaptureRole(rawRole) && !cfg.captureAssistantTurns) continue;

    const rawText = extractTextFromPayload(payload, { toolMaxChars: cfg.captureToolMaxChars });
    const decision = shouldCaptureText(rawText, role, cfg);
    if (!decision.shouldCapture) continue;
    turns.push({ role, text: decision.text });
  }
  return turns;
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

function hasCaptureKeyword(turns) {
  return turns.some((turn) => /\b(remember|memorize|store|save|capture|note|record)\b|记住|保存|记录|记忆/i.test(turn.text));
}

async function runInlinePath({ sessionId, state, ovSessionId, newTurns, allTurns }) {
  if (newTurns.length > 0) {
    const result = await appendTurnsBatch(ovSessionId, newTurns, state);
    if (!result.ok) {
      logError("inline_append_failed_keep_state", {
        sessionId,
        ovSessionId,
        capturedTurnCount: state.capturedTurnCount,
      });
      await saveState(state);
      noop(`pre-compact catch-up append incomplete for ${ovSessionId}; state preserved for retry`);
      return;
    }
  }

  const commit = await commitOvSession(ovSessionId);
  if (!commit) {
    // Commit failure handling (see DESIGN.md "Commit failure"): if /commit
    // fails (server unreachable, non-2xx, timeout) we MUST NOT reset
    // ovSessionId — keep state intact so the next sweep / SessionStart can
    // retry. A transient OV outage shouldn't lose a session's memory.
    logError("inline_commit_failed_keep_state", { sessionId, ovSessionId });
    await saveState(state); // bumps lastUpdatedAt only, keeps ovSessionId
    noop(`pre-compact commit attempted on ${ovSessionId}; result unavailable (state preserved for retry)`);
    return;
  }
  state.ovSessionId = null;
  await saveState(state);
  log("inline_commit", {
    sessionId,
    ovSessionId,
    appended: newTurns.length,
    totalTurns: allTurns.length,
    archived: commit.archived ?? false,
    taskId: commit.task_id,
    status: commit.status,
  });
  noop(`OpenViking session ${ovSessionId} is committed`);
}

async function main() {
  if (!cfg.autoCommitOnCompact) {
    log("skip", { stage: "init", reason: "autoCommitOnCompact disabled" });
    noop();
    return;
  }

  let input;
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    input = JSON.parse(Buffer.concat(chunks).toString());
  } catch {
    log("skip", { stage: "stdin_parse", reason: "invalid input" });
    noop();
    return;
  }

  const sessionId = input.session_id || "unknown";
  const transcriptPath = input.transcript_path || null;
  const trigger = input.trigger || "auto";
  log("start", { sessionId, transcriptPath, trigger });

  const state = await loadState(sessionId);
  const allTurns = await readTranscriptTurns(transcriptPath);

  if (allTurns.length < state.capturedTurnCount) {
    log("transcript_shrink_detected", {
      cached: state.capturedTurnCount,
      observed: allTurns.length,
    });
    state.capturedTurnCount = 0;
  }

  if (allTurns.length === 0 && !state.ovSessionId) {
    log("skip", { stage: "nothing_to_commit", reason: "no transcript and no open OV session" });
    noop();
    return;
  }

  const newTurns = allTurns.slice(state.capturedTurnCount);

  if (newTurns.length > 0 && !state.ovSessionId && cfg.captureMode === "keyword" && !hasCaptureKeyword(newTurns)) {
    log("skip", { stage: "capture_mode", reason: "keyword mode without capture trigger" });
    await saveState(state);
    noop();
    return;
  }

  const ovSessionId = resolveOvSessionId(state);
  if (!ovSessionId) {
    logError("resolve_ov_session", "failed to derive OV session id");
    noop();
    return;
  }

  if (!transcriptPath) {
    // No transcript to read, but a live OV session exists — commit it inline.
    // Server `/commit` Phase 1 (snapshot + clear live + write archive) is
    // synchronous before HTTP 200, so this is bounded.
    const commit = await commitOvSession(ovSessionId);
    if (!commit) {
      logError("commit_no_transcript_failed_keep_state", { sessionId, ovSessionId });
      await saveState(state);
      noop(`pre-compact commit attempted on ${ovSessionId}; result unavailable (state preserved for retry)`);
      return;
    }
    state.ovSessionId = null;
    await saveState(state);
    log("commit_no_transcript", {
      sessionId,
      ovSessionId,
      archived: commit.archived ?? false,
      taskId: commit.task_id,
      status: commit.status,
    });
    noop(`OpenViking session ${ovSessionId} is committed`);
    return;
  }

  await runInlinePath({ sessionId, state, ovSessionId, newTurns, allTurns });
}

main().catch((err) => { logError("uncaught", err); noop(); });
