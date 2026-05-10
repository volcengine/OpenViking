#!/usr/bin/env node

/**
 * Auto-Capture Hook Script for Codex.
 *
 * Triggered by the Stop hook.
 * Codex passes `last_assistant_message`, `transcript_path`, `session_id`, `turn_id` on stdin.
 *
 * Strategy:
 *   1. Use `last_assistant_message` directly when available (cheap path).
 *   2. Fall back to incrementally parsing `transcript_path` (rollout JSONL).
 *
 * Each captured turn opens a short-lived OpenViking session, posts the text,
 * extracts memories, then deletes the session. State per session_id tracks
 * how many transcript turns we've already consumed so we don't re-capture.
 *
 * Codex Stop output schema does NOT support `decision: "approve"`. A no-op is `{}`.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("auto-capture");

const STATE_DIR = join(tmpdir(), "openviking-codex-capture-state");

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function noop(message) {
  if (message) {
    output({ systemMessage: message });
  } else {
    output({});
  }
}

async function fetchJSON(path, init = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), cfg.captureTimeoutMs);
  try {
    const headers = { "Content-Type": "application/json" };
    if (cfg.apiKey) headers["X-API-Key"] = cfg.apiKey;
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
// State (per session_id, tracks last transcript turn index)
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
    return { capturedTurnCount: 0, lastAssistantMessageHash: null };
  }
}

async function saveState(sessionId, state) {
  try {
    await mkdir(STATE_DIR, { recursive: true });
    await writeFile(stateFilePath(sessionId), JSON.stringify(state));
  } catch { /* best effort */ }
}

// ---------------------------------------------------------------------------
// Capture decision
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
const CJK_CHAR_RE = /[぀-ヿ㐀-鿿豈-﫿가-힯]/;

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

  return { capture: true, reason: "semantic", text: normalized };
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
    // Codex rollout entries can be wrapped in payload.
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
    if (text.trim()) turns.push({ role, text: text.trim() });
  }
  return turns;
}

// ---------------------------------------------------------------------------
// Capture
// ---------------------------------------------------------------------------

async function captureToOpenViking(text) {
  const sessionResult = await fetchJSON("/api/v1/sessions", {
    method: "POST",
    body: JSON.stringify({}),
  });
  if (!sessionResult?.session_id) return { ok: false, reason: "session_create_failed" };

  const ovSessionId = sessionResult.session_id;

  await fetchJSON(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ role: "user", content: text }),
  });

  const commit = await fetchJSON(
    `/api/v1/sessions/${encodeURIComponent(ovSessionId)}/commit`,
    { method: "POST", body: JSON.stringify({}) },
  );

  const extractedCount = commit?.memories_extracted && typeof commit.memories_extracted === "object"
    ? Object.values(commit.memories_extracted).reduce((a, b) => a + (typeof b === "number" ? b : 0), 0)
    : (typeof commit?.memories_extracted === "number" ? commit.memories_extracted : 0);

  return {
    ok: true,
    count: extractedCount,
    ovSessionId,
    archived: commit?.archived ?? false,
    taskId: commit?.task_id,
    status: commit?.status,
  };
}

function fastHash(text) {
  let h = 0;
  for (let i = 0; i < text.length; i++) {
    h = ((h << 5) - h + text.charCodeAt(i)) | 0;
  }
  return String(h);
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
  const lastAssistantMessage = input.last_assistant_message || null;
  log("start", {
    sessionId,
    transcriptPath,
    hasLastAssistantMessage: Boolean(lastAssistantMessage),
  });

  const health = await fetchJSON("/health");
  if (!health) {
    logError("health_check", "server unreachable or unhealthy");
    noop();
    return;
  }

  const state = await loadState(sessionId);
  let totalCaptured = 0;
  let totalExtracted = 0;

  // Strategy A: capture user turns from transcript (incremental).
  if (transcriptPath) {
    let raw;
    try {
      raw = await readFile(transcriptPath, "utf-8");
    } catch (err) {
      logError("transcript_read", err);
      raw = null;
    }

    if (raw && raw.trim()) {
      const entries = parseTranscript(raw);
      const allTurns = extractTurns(entries);
      const newTurns = allTurns.slice(state.capturedTurnCount);
      const captureTurns = cfg.captureAssistantTurns
        ? newTurns
        : newTurns.filter((t) => t.role === "user");

      log("transcript_parse", {
        totalTurns: allTurns.length,
        previouslyCaptured: state.capturedTurnCount,
        newTurns: newTurns.length,
        captureTurns: captureTurns.length,
      });

      if (captureTurns.length > 0) {
        const turnText = captureTurns.map((t) => `[${t.role}]: ${t.text}`).join("\n");
        const decision = shouldCapture(turnText);
        log("should_capture_transcript", { capture: decision.capture, reason: decision.reason });
        if (decision.capture) {
          const result = await captureToOpenViking(decision.text);
          log("openviking_capture_transcript", {
            sessionCreated: result.ok,
            ovSessionId: result.ovSessionId,
            extracted: result.count || 0,
          });
          if (result.ok) {
            totalCaptured += captureTurns.length;
            totalExtracted += result.count || 0;
          }
        }
      }

      state.capturedTurnCount = allTurns.length;
    }
  }

  // Strategy B: capture last_assistant_message (independent of transcript availability).
  // Only when (a) we want assistant turns or (b) transcript was unavailable.
  if (cfg.captureLastAssistantOnStop && lastAssistantMessage) {
    const hash = fastHash(lastAssistantMessage);
    if (hash !== state.lastAssistantMessageHash) {
      const decision = shouldCapture(lastAssistantMessage);
      log("should_capture_last_assistant", { capture: decision.capture, reason: decision.reason });
      if (decision.capture) {
        const result = await captureToOpenViking(decision.text);
        log("openviking_capture_last_assistant", {
          sessionCreated: result.ok,
          ovSessionId: result.ovSessionId,
          extracted: result.count || 0,
        });
        if (result.ok) {
          totalCaptured += 1;
          totalExtracted += result.count || 0;
        }
      }
      state.lastAssistantMessageHash = hash;
    } else {
      log("skip", { stage: "last_assistant_dedup", reason: "same hash as last capture" });
    }
  }

  await saveState(sessionId, state);

  if (totalExtracted > 0) {
    log("done", { captured: totalCaptured, extracted: totalExtracted });
    noop(`captured ${totalCaptured} turn(s), extracted ${totalExtracted} memory item(s)`);
    return;
  }

  if (totalCaptured > 0) {
    log("done", { captured: totalCaptured, extracted: 0 });
  }
  noop();
}

main().catch((err) => { logError("uncaught", err); noop(); });
