#!/usr/bin/env node

/**
 * Auto-Capture Hook Script for Claude Code
 *
 * Triggered by Stop hook.
 * Reads transcript_path from stdin → extracts INCREMENTAL conversation
 * (only new turns since last capture) → keeps user turns by default →
 * sends to OpenViking session/extract pipeline → memories stored automatically.
 *
 * Incremental tracking: uses a state file per session_id to record how many
 * turns have been captured. Only new turns are processed on each invocation.
 *
 * Ported from openclaw-plugin/ auto-capture logic in context-engine.ts + text-utils.ts.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("auto-capture");

// State directory for incremental tracking
const STATE_DIR = join(tmpdir(), "openviking-cc-capture-state");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function approve(msg) {
  const out = { decision: "approve" };
  if (msg) out.systemMessage = msg;
  output(out);
}

async function fetchJSON(path, init = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), cfg.captureTimeoutMs);
  try {
    const headers = { "Content-Type": "application/json" };
    if (cfg.apiKey) headers["X-API-Key"] = cfg.apiKey;
    if (cfg.agentId) headers["X-OpenViking-Agent"] = cfg.agentId;
    const res = await fetch(`${cfg.baseUrl}${path}`, { ...init, headers, signal: controller.signal });
    const body = await res.json();
    if (!res.ok || body.status === "error") return null;
    return body.result ?? body;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// Incremental state tracking
// ---------------------------------------------------------------------------

function stateFilePath(sessionId) {
  const safe = sessionId.replace(/[^a-zA-Z0-9_-]/g, "_");
  return join(STATE_DIR, `${safe}.json`);
}

async function loadState(sessionId) {
  try {
    const data = await readFile(stateFilePath(sessionId), "utf-8");
    return JSON.parse(data);
  } catch {
    return { capturedTurnCount: 0 };
  }
}

async function saveState(sessionId, state) {
  try {
    await mkdir(STATE_DIR, { recursive: true });
    await writeFile(stateFilePath(sessionId), JSON.stringify(state));
  } catch { /* best effort */ }
}

// ---------------------------------------------------------------------------
// Text processing (ported from text-utils.ts)
// ---------------------------------------------------------------------------

const MEMORY_TRIGGERS = [
  /remember|preference|prefer|important|decision|decided|always|never/i,
  /记住|偏好|喜欢|喜爱|崇拜|讨厌|害怕|重要|决定|总是|永远|优先|习惯|爱好|擅长|最爱|不喜欢/i,
  /[\w.-]+@[\w.-]+\.\w+/,
  /\+\d{10,}/,
  /(?:我|my)\s*(?:是|叫|名字|name|住在|live|来自|from|生日|birthday|电话|phone|邮箱|email)/i,
  /(?:我|i)\s*(?:喜欢|崇拜|讨厌|害怕|擅长|不会|爱|恨|想要|需要|希望|觉得|认为|相信)/i,
  /(?:favorite|favourite|love|hate|enjoy|dislike|admire|idol|fan of)/i,
];

const RELEVANT_MEMORIES_BLOCK_RE = /<relevant-memories>[\s\S]*?<\/relevant-memories>/gi;
const COMMAND_TEXT_RE = /^\/[a-z0-9_-]{1,64}\b/i;
const NON_CONTENT_TEXT_RE = /^[\p{P}\p{S}\s]+$/u;
const CJK_CHAR_RE = /[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uac00-\ud7af]/;

function sanitize(text) {
  return text
    .replace(RELEVANT_MEMORIES_BLOCK_RE, " ")
    .replace(/\u0000/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function shouldCapture(text) {
  const normalized = sanitize(text);
  if (!normalized) return { capture: false, reason: "empty", text: "" };

  const compact = normalized.replace(/\s+/g, "");
  const minLen = CJK_CHAR_RE.test(compact) ? 4 : 10;
  if (compact.length < minLen || normalized.length > cfg.captureMaxLength) {
    return { capture: false, reason: "length_out_of_range", text: normalized };
  }

  if (COMMAND_TEXT_RE.test(normalized)) {
    return { capture: false, reason: "command", text: normalized };
  }

  if (NON_CONTENT_TEXT_RE.test(normalized)) {
    return { capture: false, reason: "non_content", text: normalized };
  }

  if (cfg.captureMode === "keyword") {
    for (const trigger of MEMORY_TRIGGERS) {
      if (trigger.test(normalized)) {
        return { capture: true, reason: `trigger:${trigger}`, text: normalized };
      }
    }
    return { capture: false, reason: "no_trigger", text: normalized };
  }

  // semantic mode — always capture
  return { capture: true, reason: "semantic", text: normalized };
}

// ---------------------------------------------------------------------------
// Transcript parsing
// ---------------------------------------------------------------------------

function parseTranscript(content) {
  try {
    const data = JSON.parse(content);
    if (Array.isArray(data)) return data;
  } catch { /* not a JSON array */ }

  const lines = content.split("\n").filter(l => l.trim());
  const messages = [];
  for (const line of lines) {
    try { messages.push(JSON.parse(line)); } catch { /* skip */ }
  }
  return messages;
}

function extractAllTurns(messages) {
  const turns = [];
  for (const msg of messages) {
    if (!msg || typeof msg !== "object") continue;

    let role = msg.role;
    let text = "";

    if (typeof msg.content === "string") {
      text = msg.content;
    } else if (Array.isArray(msg.content)) {
      const textParts = msg.content
        .filter(b => b && b.type === "text" && typeof b.text === "string")
        .map(b => b.text);
      text = textParts.join("\n");
    } else if (typeof msg.message === "object" && msg.message) {
      const inner = msg.message;
      role = inner.role || role;
      if (typeof inner.content === "string") {
        text = inner.content;
      } else if (Array.isArray(inner.content)) {
        const textParts = inner.content
          .filter(b => b && b.type === "text" && typeof b.text === "string")
          .map(b => b.text);
        text = textParts.join("\n");
      }
    }

    if (role !== "user" && role !== "assistant") continue;
    if (text.trim()) {
      turns.push({ role, text: text.trim() });
    }
  }
  return turns;
}

// ---------------------------------------------------------------------------
// OpenViking session capture
// ---------------------------------------------------------------------------

async function captureToOpenViking(text) {
  const sessionResult = await fetchJSON("/api/v1/sessions", {
    method: "POST",
    body: JSON.stringify({}),
  });
  if (!sessionResult?.session_id) return { ok: false, reason: "session_create_failed" };

  const sessionId = sessionResult.session_id;
  try {
    await fetchJSON(`/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: "POST",
      body: JSON.stringify({ role: "user", content: text }),
    });

    const extracted = await fetchJSON(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/extract`,
      { method: "POST", body: JSON.stringify({}) }
    );

    return { ok: true, count: Array.isArray(extracted) ? extracted.length : 0 };
  } finally {
    await fetchJSON(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    }).catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  if (!cfg.autoCapture) {
    log("skip", { stage: "init", reason: "autoCapture disabled" });
    approve();
    return;
  }

  let input;
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    input = JSON.parse(Buffer.concat(chunks).toString());
  } catch {
    log("skip", { stage: "stdin_parse", reason: "invalid input" });
    approve();
    return;
  }

  const transcriptPath = input.transcript_path;
  const sessionId = input.session_id || "unknown";
  log("start", { sessionId, transcriptPath });

  // Quick health check
  const health = await fetchJSON("/health");
  if (!health) {
    logError("health_check", "server unreachable or unhealthy");
    approve();
    return;
  }

  if (!transcriptPath) {
    log("skip", { stage: "input_check", reason: "no transcript_path" });
    approve();
    return;
  }

  let transcriptContent;
  try {
    transcriptContent = await readFile(transcriptPath, "utf-8");
  } catch (err) {
    logError("transcript_read", err);
    approve();
    return;
  }

  if (!transcriptContent.trim()) {
    log("skip", { stage: "transcript_read", reason: "empty transcript" });
    approve();
    return;
  }

  // Parse transcript and extract all turns
  const messages = parseTranscript(transcriptContent);
  const allTurns = extractAllTurns(messages);
  if (allTurns.length === 0) {
    log("skip", { stage: "transcript_parse", reason: "no user/assistant turns found" });
    approve();
    return;
  }

  // Load incremental state — skip already-captured turns
  const state = await loadState(sessionId);
  const newTurns = allTurns.slice(state.capturedTurnCount);
  const captureTurns = cfg.captureAssistantTurns
    ? newTurns
    : newTurns.filter(turn => turn.role === "user");
  log("transcript_parse", {
    totalTurns: allTurns.length,
    previouslyCaptured: state.capturedTurnCount,
    newTurns: newTurns.length,
    captureTurns: captureTurns.length,
    assistantTurnsSkipped: newTurns.length - captureTurns.length,
  });

  if (newTurns.length === 0) {
    log("skip", { stage: "incremental_check", reason: "no new turns" });
    approve();
    return;
  }

  if (captureTurns.length === 0) {
    await saveState(sessionId, { capturedTurnCount: allTurns.length });
    log("state_update", { newCapturedTurnCount: allTurns.length, reason: "assistant_only_increment" });
    approve();
    return;
  }

  // Format only the capturable turns
  const turnText = captureTurns.map(t => `[${t.role}]: ${t.text}`).join("\n");

  // Check capture decision
  const decision = shouldCapture(turnText);
  log("should_capture", {
    capture: decision.capture,
    reason: decision.reason,
    textPreview: decision.text.slice(0, 100),
  });

  if (!decision.capture) {
    await saveState(sessionId, { capturedTurnCount: allTurns.length });
    approve();
    return;
  }

  // Send to OpenViking
  const result = await captureToOpenViking(decision.text);
  log("openviking_capture", { sessionCreated: result.ok, extractedCount: result.count || 0 });

  // Update state
  await saveState(sessionId, { capturedTurnCount: allTurns.length });
  log("state_update", { newCapturedTurnCount: allTurns.length });

  if (result.ok && result.count > 0) {
    log("done", { captureTurns: captureTurns.length, extractedCount: result.count });
    approve(`captured ${captureTurns.length} turns, extracted ${result.count} memories`);
  } else {
    approve();
  }
}

main().catch((err) => { logError("uncaught", err); approve(); });
