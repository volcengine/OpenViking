#!/usr/bin/env node

/**
 * PreCompact hook for Codex.
 *
 * Codex is about to summarize/compact the conversation. We commit the
 * long-lived OpenViking session for this codex session_id (Stop hooks
 * have already been appending turns), which triggers OV's memory
 * extractor on the full pre-compact transcript.
 *
 * Catch-up: if the transcript has new turns the Stop hook hasn't
 * appended yet, we append them before committing.
 *
 * After commit, we clear ovSessionId from state but keep
 * capturedTurnCount so post-compact Stop hooks don't re-capture pre-
 * compact turns. The next Stop will create a fresh OV session for the
 * post-compact half of the conversation.
 *
 * PreCompact output schema accepts {} as a no-op.
 */

import { readFile } from "node:fs/promises";
import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { loadState, saveState } from "./session-state.mjs";

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
    if (cfg.account) headers["X-OpenViking-Account"] = cfg.account;
    if (cfg.user) headers["X-OpenViking-User"] = cfg.user;
    if (cfg.agentId) headers["X-OpenViking-Agent"] = cfg.agentId;
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

function extractTextFromContent(content) {
  if (!content) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter((b) => b && (b.type === "text" || b.type === "input_text" || b.type === "output_text"))
      .map((b) => b.text || "")
      .join("\n");
  }
  return "";
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
    let role = payload.role;
    let text = "";

    if (typeof payload.content === "string") {
      text = payload.content;
    } else if (Array.isArray(payload.content)) {
      text = extractTextFromContent(payload.content);
    } else if (payload.message && typeof payload.message === "object") {
      role = payload.message.role || role;
      text = typeof payload.message.content === "string"
        ? payload.message.content
        : extractTextFromContent(payload.message.content);
    }

    if (role !== "user" && role !== "assistant") continue;
    if (role === "assistant" && !cfg.captureAssistantTurns) continue;
    const trimmed = text.trim();
    if (!trimmed) continue;

    const capped = trimmed.length > cfg.captureMaxLength
      ? trimmed.slice(0, cfg.captureMaxLength)
      : trimmed;
    turns.push({ role, text: capped });
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

async function ensureOvSession(state) {
  if (state.ovSessionId) return state.ovSessionId;
  const created = await fetchJSON("/api/v1/sessions", {
    method: "POST",
    body: JSON.stringify({}),
  });
  if (!created?.session_id) return null;
  state.ovSessionId = created.session_id;
  return state.ovSessionId;
}

async function appendTurns(ovSessionId, turns) {
  let appended = 0;
  for (const turn of turns) {
    const result = await fetchJSON(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}/messages`, {
      method: "POST",
      body: JSON.stringify({ role: turn.role, content: turn.text }),
    });
    if (!result) break;
    appended += 1;
  }
  return appended;
}

function countExtracted(commit) {
  if (!commit?.memories_extracted) return 0;
  if (typeof commit.memories_extracted === "number") return commit.memories_extracted;
  if (typeof commit.memories_extracted === "object") {
    return Object.values(commit.memories_extracted).reduce(
      (a, b) => a + (typeof b === "number" ? b : 0),
      0,
    );
  }
  return 0;
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

  const health = await fetchJSON("/health");
  if (!health) {
    logError("health_check", "server unreachable");
    noop();
    return;
  }

  const state = await loadState(sessionId);
  const allTurns = await readTranscriptTurns(transcriptPath);
  const newTurns = allTurns.slice(state.capturedTurnCount);

  log("transcript_parse", {
    totalTurns: allTurns.length,
    previouslyCaptured: state.capturedTurnCount,
    newTurns: newTurns.length,
  });

  if (allTurns.length === 0 && !state.ovSessionId) {
    log("skip", { stage: "nothing_to_commit", reason: "no transcript and no open OV session" });
    noop();
    return;
  }

  if (newTurns.length > 0 && !state.ovSessionId && cfg.captureMode === "keyword" && !hasCaptureKeyword(newTurns)) {
    log("skip", { stage: "capture_mode", reason: "keyword mode without capture trigger" });
    await saveState(state);
    noop();
    return;
  }

  if (newTurns.length > 0) {
    const ovSessionId = await ensureOvSession(state);
    if (!ovSessionId) {
      logError("ensure_ov_session", "failed to create OV session for catch-up");
      noop();
      return;
    }
    const added = await appendTurns(ovSessionId, newTurns);
    state.capturedTurnCount += added;
    log("appended_catchup", { ovSessionId, added });
    if (added < newTurns.length) {
      logError("append_failed_keep_state", { ovSessionId, attempted: newTurns.length, added });
      await saveState(state);
      noop(`pre-compact catch-up append incomplete for ${ovSessionId}; state preserved for retry`);
      return;
    }
  }

  if (!state.ovSessionId) {
    log("skip", { stage: "commit", reason: "no OV session for this codex session" });
    await saveState(state);
    noop();
    return;
  }

  const ovSessionId = state.ovSessionId;
  const commit = await fetchJSON(
    `/api/v1/sessions/${encodeURIComponent(ovSessionId)}/commit`,
    { method: "POST", body: JSON.stringify({}) },
  );

  // Commit failure handling (see DESIGN.md "Commit failure"): if /commit
  // fails (server unreachable, non-2xx, timeout) we MUST NOT reset
  // ovSessionId — keep state intact so the next sweep / SessionStart can
  // retry. A transient OV outage shouldn't lose a session's memory.
  if (!commit) {
    logError("commit_failed_keep_state", { ovSessionId });
    await saveState(state); // bumps lastUpdatedAt only, keeps ovSessionId
    noop(`pre-compact commit attempted on ${ovSessionId}; result unavailable (state preserved for retry)`);
    return;
  }

  const extracted = countExtracted(commit);
  log("commit", {
    ovSessionId,
    extracted,
    archived: commit.archived ?? false,
    taskId: commit.task_id,
    status: commit.status,
  });

  // Reset OV session for the post-compact half. Keep capturedTurnCount so
  // we don't re-capture pre-compact turns when Stop fires next.
  state.ovSessionId = null;
  await saveState(state);

  noop(
    `pre-compact commit: ${ovSessionId} → ${extracted} memory item(s) extracted${commit.archived ? " (archived)" : ""}`,
  );
}

function hasCaptureKeyword(turns) {
  return turns.some((turn) => /\b(remember|memorize|store|save|capture|note|record)\b|记住|保存|记录|记忆/i.test(turn.text));
}

main().catch((err) => { logError("uncaught", err); noop(); });
