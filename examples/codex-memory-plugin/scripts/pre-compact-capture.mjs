#!/usr/bin/env node

/**
 * PreCompact hook for Codex.
 *
 * Codex is about to summarize/compact the conversation, after which it will
 * rewrite/truncate `transcript_path`. We need every pre-compact turn to land
 * in OpenViking and the OV session to be committed (so the extractor runs on
 * the full pre-compact transcript).
 *
 * Hybrid behavior (compat-first):
 *   - Small backlog (newTurns.length <= captureMaxTurnsPerStop): inline path
 *     1. Append every pending turn to the live OV session.
 *     2. Check OV session size. If it exceeds the commit budget, spawn
 *        `commit-session.mjs` and rotate `ovSessionId` to a
 *        `cx-...-part-<ts>` so the inline path doesn't stall codex.
 *     3. Otherwise inline `/commit`. On success clear `state.ovSessionId`.
 *
 *   - Large backlog (newTurns.length > captureMaxTurnsPerStop): snapshot+async
 *     1. Hook synchronously `fs.copyFile`s `transcript_path` to a snapshot
 *        under `~/.openviking/codex-plugin-snapshots/<id>-<ts>.jsonl`
 *        (~10ms regardless of size — decouples the worker from codex's
 *        transcript rewrite).
 *     2. Hook spawns detached `capture-transcript-worker.mjs` against the
 *        snapshot with `--start-index <capturedTurnCount> --end-index <total>
 *        --cleanup-snapshot`. Worker appends every unappended turn and
 *        commits.
 *     3. Hook clears `state.ovSessionId` and bumps `capturedTurnCount = total`.
 *
 * The post-compact Stop re-derives `cx-<codex-session-id>` and starts a new
 * live session on the server (POST /messages auto-creates).
 *
 * If `transcript_path` is missing but a live OV session exists, the hook
 * spawns `commit-session.mjs` (commit-only path — no append needed).
 *
 * Inline-path failure: state preserved; the next PreCompact or SessionStart
 * commit picks up the still-open server session.
 *
 * Async-path failure (worker crash, OV unreachable): next Stop sees
 * `state.ovSessionId === null`, lazily re-derives `cx-<codex-session-id>`,
 * and continues appending; the next commit picks up the still-open server
 * session. Snapshot files for failed workers stay on disk under
 * `~/.openviking/codex-plugin-snapshots/` for diagnosis.
 *
 * PreCompact output schema accepts {} as a no-op.
 */

import { copyFile, mkdir, readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import {
  extractTextFromPayload,
  isAssistantSideCaptureRole,
  normalizeCaptureRole,
  shouldCaptureText,
} from "./capture-utils.mjs";
import { startDetachedScript } from "./background-jobs.mjs";
import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import {
  loadState,
  resolveOvSessionId,
  rotateOvSessionId,
  saveState,
} from "./session-state.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("pre-compact");

const SNAPSHOT_DIR = process.env.OPENVIKING_CODEX_SNAPSHOT_DIR
  || join(homedir(), ".openviking", "codex-plugin-snapshots");

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

async function appendOneTurn(ovSessionId, turn) {
  const body = { role: turn.role, content: turn.text };
  if (cfg.peerId) body.peer_id = cfg.peerId;
  return fetchJSON(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}/messages`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

async function commitOvSession(ovSessionId) {
  if (!ovSessionId) return null;
  return fetchJSON(
    `/api/v1/sessions/${encodeURIComponent(ovSessionId)}/commit`,
    { method: "POST", body: JSON.stringify({}) },
  );
}

async function getOvSessionMeta(ovSessionId) {
  if (!ovSessionId) return null;
  return fetchJSON(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}`);
}

function sessionExceedsCommitBudget(meta) {
  if (!meta) return false;
  const messageCount = Number(meta.message_count || 0);
  const pendingTokens = Number(meta.pending_tokens || 0);
  return (
    messageCount > cfg.maxLiveMessagesOnCompact ||
    pendingTokens > cfg.maxPendingTokensOnCompact
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

function safeId(value) {
  return String(value).replace(/[^a-zA-Z0-9_-]/g, "_");
}

async function snapshotTranscript(transcriptPath, sessionId) {
  await mkdir(SNAPSHOT_DIR, { recursive: true });
  const snapshotPath = join(
    SNAPSHOT_DIR,
    `${safeId(sessionId)}-${Date.now().toString(36)}.jsonl`,
  );
  await copyFile(transcriptPath, snapshotPath);
  return snapshotPath;
}

function hasCaptureKeyword(turns) {
  return turns.some((turn) => /\b(remember|memorize|store|save|capture|note|record)\b|记住|保存|记录|记忆/i.test(turn.text));
}

async function runAsyncSnapshotPath({ sessionId, transcriptPath, allTurns, state, ovSessionId }) {
  let snapshotPath;
  try {
    snapshotPath = await snapshotTranscript(transcriptPath, sessionId);
  } catch (err) {
    logError("snapshot_failed", err);
    noop();
    return;
  }

  const previouslyCaptured = state.capturedTurnCount;
  const pid = startDetachedScript("capture-transcript-worker.mjs", [
    "--session-id", sessionId,
    "--transcript", snapshotPath,
    "--ov-session-id", ovSessionId,
    "--start-index", String(previouslyCaptured),
    "--end-index", String(allTurns.length),
    "--batch-size", String(cfg.backgroundCaptureBatchSize),
    "--cleanup-snapshot",
  ]);

  state.capturedTurnCount = allTurns.length;
  state.ovSessionId = null;
  await saveState(state);

  log("async_scheduled", {
    sessionId,
    ovSessionId,
    snapshotPath,
    startIndex: previouslyCaptured,
    endIndex: allTurns.length,
    pid,
  });
  noop(`OpenViking commit scheduled for ${ovSessionId} (pid ${pid ?? "?"})`);
}

async function runInlinePath({ sessionId, state, ovSessionId, newTurns, allTurns }) {
  for (const turn of newTurns) {
    const result = await appendOneTurn(ovSessionId, turn);
    if (!result) {
      logError("inline_append_failed_keep_state", {
        sessionId,
        ovSessionId,
        capturedTurnCount: state.capturedTurnCount,
      });
      await saveState(state);
      noop();
      return;
    }
    state.capturedTurnCount += 1;
    await saveState(state);
  }

  const sessionMeta = await getOvSessionMeta(ovSessionId);
  if (sessionExceedsCommitBudget(sessionMeta)) {
    const messageCount = Number(sessionMeta.message_count || 0);
    const pendingTokens = Number(sessionMeta.pending_tokens || 0);
    const pid = startDetachedScript("commit-session.mjs", [
      "--ov-session-id", ovSessionId,
      "--reason", "precompact_oversize_inline",
    ]);
    const nextOvSessionId = rotateOvSessionId(state, {
      reason: "precompact_commit_budget",
      messageCount,
      pendingTokens,
      maxLiveMessagesOnCompact: cfg.maxLiveMessagesOnCompact,
      maxPendingTokensOnCompact: cfg.maxPendingTokensOnCompact,
      backgroundPid: pid,
    });
    await saveState(state);
    log("inline_background_commit_started", {
      sessionId,
      ovSessionId,
      nextOvSessionId,
      messageCount,
      pendingTokens,
      pid,
    });
    noop(`OpenViking commit scheduled for ${ovSessionId} (pid ${pid ?? "?"}); next session ${nextOvSessionId}`);
    return;
  }

  const commit = await commitOvSession(ovSessionId);
  if (!commit) {
    logError("inline_commit_failed_keep_state", { sessionId, ovSessionId });
    await saveState(state);
    noop();
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
    // No transcript to snapshot or read, but a live OV session exists.
    // Schedule a commit-only worker.
    const pid = startDetachedScript("commit-session.mjs", [
      "--ov-session-id", ovSessionId,
      "--reason", "precompact_no_transcript",
    ]);
    state.ovSessionId = null;
    await saveState(state);
    log("commit_only_scheduled", { ovSessionId, pid });
    noop(`OpenViking commit scheduled for ${ovSessionId} (pid ${pid ?? "?"})`);
    return;
  }

  if (newTurns.length > cfg.captureMaxTurnsPerStop) {
    log("path_choice", {
      branch: "async_snapshot",
      newTurnCount: newTurns.length,
      threshold: cfg.captureMaxTurnsPerStop,
    });
    await runAsyncSnapshotPath({ sessionId, transcriptPath, allTurns, state, ovSessionId });
    return;
  }

  log("path_choice", {
    branch: "inline",
    newTurnCount: newTurns.length,
    threshold: cfg.captureMaxTurnsPerStop,
  });
  await runInlinePath({ sessionId, state, ovSessionId, newTurns, allTurns });
}

main().catch((err) => { logError("uncaught", err); noop(); });
