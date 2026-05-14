#!/usr/bin/env node

/**
 * Stop hook for Codex (turn end).
 *
 * Codex passes JSON on stdin including session_id, transcript_path,
 * last_assistant_message. Stop fires per turn — NOT at session end.
 *
 * Strategy:
 *   1. For this codex session_id, lazily create one long-lived OpenViking
 *      session and remember it in state. Do NOT commit per turn.
 *   2. Read transcript_path, parse JSONL rollout, append every new
 *      user/assistant turn since last capture via add_message.
 *
 * Commit happens in two other places, never here:
 *   - PreCompact hook (deterministic, before context compaction)
 *   - SessionStart hook (active-window heuristic + idle-TTL sweep at tail)
 *
 * Stop output schema accepts {} as a no-op.
 *
 * Note: we deliberately do NOT run an idle-TTL sweep here. State-write-on-
 * every-turn already gives us the freshness signal we need; running the
 * sweep once per session start (in session-start-commit.mjs) is the right
 * cadence. See DESIGN.md §5 ("Sweep trigger").
 */

import { readFile } from "node:fs/promises";
import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { loadState, saveState } from "./session-state.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("auto-capture");

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

// ---------------------------------------------------------------------------
// Transcript parsing (JSONL rollout)
// ---------------------------------------------------------------------------

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
  } catch { /* not a JSON array */ }
  const lines = content.split("\n").filter((l) => l.trim());
  const out = [];
  for (const line of lines) {
    try { out.push(JSON.parse(line)); } catch { /* skip */ }
  }
  return out;
}

function extractTurns(rolloutEntries) {
  const turns = [];
  for (const entry of rolloutEntries) {
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

// ---------------------------------------------------------------------------
// OpenViking session ops
// ---------------------------------------------------------------------------

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

async function commitOvSession(ovSessionId) {
  if (!ovSessionId) return null;
  return fetchJSON(
    `/api/v1/sessions/${encodeURIComponent(ovSessionId)}/commit`,
    { method: "POST", body: JSON.stringify({}) },
  );
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

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  if (!cfg.autoCapture) {
    log("skip", { stage: "init", reason: "autoCapture disabled" });
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
  log("start", { sessionId, transcriptPath });

  const health = await fetchJSON("/health");
  if (!health) {
    logError("health_check", "server unreachable or unhealthy");
    noop();
    return;
  }

  const state = await loadState(sessionId);
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
  if (newTurns.length > 0) {
    const ovSessionId = await ensureOvSession(state);
    if (!ovSessionId) {
      logError("ensure_ov_session", "failed to create OV session");
    } else {
      added = await appendTurns(ovSessionId, newTurns);
      state.capturedTurnCount += added;
      log("appended", { ovSessionId, added });
    }
  }

  await saveState(state);

  // could also sweep here, deliberately not — see header comment + DESIGN.md §5.

  if (added > 0) {
    noop(`appended ${added} turn(s) to OpenViking session ${state.ovSessionId}`);
  } else {
    noop();
  }
}

function hasCaptureKeyword(turns) {
  return turns.some((turn) => /\b(remember|memorize|store|save|capture|note|record)\b|记住|保存|记录|记忆/i.test(turn.text));
}

main().catch((err) => { logError("uncaught", err); noop(); });
