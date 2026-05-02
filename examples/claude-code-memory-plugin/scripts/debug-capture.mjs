#!/usr/bin/env node

/**
 * Debug Capture Pipeline — standalone test script.
 *
 * Usage:
 *   node scripts/debug-capture.mjs /path/to/transcript.jsonl [session-id]
 *   echo "remember I like Rust" | node scripts/debug-capture.mjs --stdin
 *
 * Runs the same capture pipeline as auto-capture.mjs but prints every
 * intermediate stage to stdout in a human-readable format instead of
 * silently returning a hook decision.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { loadConfig } from "./config.mjs";

// ---------------------------------------------------------------------------
// ANSI helpers
// ---------------------------------------------------------------------------

const C = {
  reset: "\x1b[0m",
  dim: "\x1b[2m",
  bold: "\x1b[1m",
  red: "\x1b[31m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  cyan: "\x1b[36m",
};

function header(title) {
  console.log(`\n${C.cyan}${C.bold}═══ ${title} ═══${C.reset}\n`);
}

function ok(msg) { console.log(`${C.green}\u2713${C.reset} ${msg}`); }
function warn(msg) { console.log(`${C.yellow}\u26A0${C.reset} ${msg}`); }
function fail(msg) { console.log(`${C.red}\u2717${C.reset} ${msg}`); }
function dim(msg) { console.log(`${C.dim}${msg}${C.reset}`); }

function preview(text, maxLen) {
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen) + "...";
}

// ---------------------------------------------------------------------------
// State directory for incremental tracking
// ---------------------------------------------------------------------------

const STATE_DIR = join(tmpdir(), "openviking-cc-capture-state");

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
// Text processing (copied from auto-capture.mjs)
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
// Transcript parsing (copied from auto-capture.mjs)
// ---------------------------------------------------------------------------

function parseTranscript(content) {
  try {
    const data = JSON.parse(content);
    if (Array.isArray(data)) return { messages: data, format: "JSON array" };
  } catch { /* not a JSON array */ }

  const lines = content.split("\n").filter(l => l.trim());
  const messages = [];
  for (const line of lines) {
    try { messages.push(JSON.parse(line)); } catch { /* skip */ }
  }
  return { messages, format: "JSONL" };
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
// fetchJSON — verbose version for debugging
// ---------------------------------------------------------------------------

async function fetchJSON(path, init = {}) {
  const url = `${cfg.baseUrl}${path}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), cfg.captureTimeoutMs);
  try {
    const headers = { "Content-Type": "application/json" };
    if (cfg.apiKey) headers["X-API-Key"] = cfg.apiKey;
    if (cfg.agentId) headers["X-OpenViking-Agent"] = cfg.agentId;
    if (cfg.account) headers["X-OpenViking-Account"] = cfg.account;
    if (cfg.user) headers["X-OpenViking-User"] = cfg.user;
    const res = await fetch(url, { ...init, headers, signal: controller.signal });
    const body = await res.json();
    dim(`  ${init.method || "GET"} ${path} -> ${res.status}`);
    if (!res.ok || body.status === "error") {
      fail(`  Response error: ${JSON.stringify(body).slice(0, 300)}`);
      return null;
    }
    const result = body.result ?? body;
    dim(`  Response: ${JSON.stringify(result).slice(0, 300)}`);
    return result;
  } catch (err) {
    fail(`  Fetch failed: ${url} — ${err?.message || err}`);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------------------
// captureToOpenViking — verbose version showing each step
// ---------------------------------------------------------------------------

async function captureToOpenViking(text) {
  header("Session Pipeline");

  // Step 1: Create session
  console.log("Step 1: Creating session...");
  const sessionResult = await fetchJSON("/api/v1/sessions", {
    method: "POST",
    body: JSON.stringify({}),
  });
  if (!sessionResult?.session_id) {
    fail("Session creation failed — no session_id returned");
    return { ok: false, reason: "session_create_failed" };
  }
  ok(`Session created: ${sessionResult.session_id}`);

  const sessionId = sessionResult.session_id;
  try {
    // Step 2: Add message
    console.log("\nStep 2: Adding message...");
    const addResult = await fetchJSON(`/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: "POST",
      body: JSON.stringify({ role: "user", content: text }),
    });
    if (addResult) {
      ok("Message added to session");
    } else {
      warn("addMessage returned null (may still work)");
    }

    // Step 3: Extract
    console.log("\nStep 3: Extracting memories...");
    const extracted = await fetchJSON(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/extract`,
      { method: "POST", body: JSON.stringify({}) },
    );

    if (extracted) {
      ok("Extraction completed");
    } else {
      fail("Extraction returned null");
    }

    // Show extracted memories
    header("Extracted Memories");
    const memories = Array.isArray(extracted) ? extracted : [];
    if (memories.length === 0) {
      warn("No memories extracted");
    } else {
      ok(`${memories.length} memor${memories.length === 1 ? "y" : "ies"} extracted:`);
      for (let i = 0; i < memories.length; i++) {
        const m = memories[i];
        console.log(`\n  ${C.green}[${i + 1}]${C.reset}`);
        if (m.id) dim(`    id:       ${m.id}`);
        if (m.content) console.log(`    content:  ${preview(String(m.content), 200)}`);
        if (m.category) dim(`    category: ${m.category}`);
        if (m.score != null) dim(`    score:    ${m.score}`);
        // Print any other fields
        for (const key of Object.keys(m)) {
          if (!["id", "content", "category", "score"].includes(key)) {
            dim(`    ${key}: ${JSON.stringify(m[key]).slice(0, 150)}`);
          }
        }
      }
    }

    return { ok: true, count: memories.length };
  } finally {
    // Cleanup session
    dim("\nCleaning up session...");
    await fetchJSON(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    }).catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// stdin reader
// ---------------------------------------------------------------------------

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString();
}

// ---------------------------------------------------------------------------
// Config (loaded at module level like auto-capture.mjs)
// ---------------------------------------------------------------------------

const cfg = loadConfig();

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  const args = process.argv.slice(2);
  const isStdin = args.includes("--stdin");

  if (args.length === 0 && !isStdin) {
    console.error(`Usage:
  node scripts/debug-capture.mjs /path/to/transcript.jsonl [session-id]
  echo "remember I like Rust" | node scripts/debug-capture.mjs --stdin`);
    process.exit(1);
  }

  // ── Stage 1: Config summary ──
  header("Config Summary");
  console.log(`  mode:             ${cfg.mode}`);
  console.log(`  baseUrl:          ${cfg.baseUrl}`);
  console.log(`  captureMode:      ${cfg.captureMode}`);
  console.log(`  captureMaxLength: ${cfg.captureMaxLength}`);
  console.log(`  captureTimeoutMs: ${cfg.captureTimeoutMs}`);
  console.log(`  captureAssistant: ${cfg.captureAssistantTurns}`);
  console.log(`  debug:            ${cfg.debug}`);
  console.log(`  agentId:          ${cfg.agentId}`);
  console.log(`  timeoutMs:        ${cfg.timeoutMs}`);
  console.log(`  clientConfig:     ${cfg.configPath}`);
  if (cfg.mode === "local") {
    console.log(`  serverConfig:     ${cfg.serverConfigPath}`);
    if (cfg.serverConfigError) {
      warn(`  local ov.conf fallback unavailable: ${cfg.serverConfigError}`);
    }
  }

  let allTurns;
  let sessionId;
  let skipIncremental = false;
  let transcriptFormat;

  if (isStdin) {
    // ── stdin mode ──
    header("Transcript Parsing");
    const stdinText = await readStdin();
    if (!stdinText.trim()) {
      fail("No input received on stdin");
      process.exit(1);
    }
    allTurns = [{ role: "user", text: stdinText.trim() }];
    sessionId = "stdin-debug";
    skipIncremental = true;
    transcriptFormat = "stdin text";
    ok(`Format: ${transcriptFormat}`);
    ok(`Total turns: ${allTurns.length}`);
    dim(`  Text: ${preview(stdinText.trim(), 100)}`);
  } else {
    // ── file mode ──
    const transcriptPath = args[0];
    sessionId = args[1] || "debug-" + Date.now();

    header("Transcript Parsing");
    let transcriptContent;
    try {
      transcriptContent = await readFile(transcriptPath, "utf-8");
    } catch (err) {
      fail(`Failed to read transcript: ${transcriptPath} — ${err?.message || err}`);
      process.exit(1);
    }

    if (!transcriptContent.trim()) {
      fail("Transcript file is empty");
      process.exit(1);
    }

    const { messages, format } = parseTranscript(transcriptContent);
    transcriptFormat = format;
    allTurns = extractAllTurns(messages);

    ok(`Format: ${transcriptFormat}`);
    ok(`Total messages parsed: ${messages.length}`);
    ok(`Total turns extracted: ${allTurns.length}`);
    dim(`  Session ID: ${sessionId}`);
  }

  if (allTurns.length === 0) {
    warn("No turns to process");
    process.exit(0);
  }

  // ── Stage 3: Incremental state ──
  header("Incremental State");
  let newTurns;
  let captureTurns;
  if (skipIncremental) {
    dim("  Skipped (stdin mode)");
    newTurns = allTurns;
    ok(`New turns to process: ${newTurns.length}`);
  } else {
    const state = await loadState(sessionId);
    dim(`  State file: ${stateFilePath(sessionId)}`);
    console.log(`  Previously captured: ${state.capturedTurnCount}`);
    newTurns = allTurns.slice(state.capturedTurnCount);
    ok(`New turns to process: ${newTurns.length}`);
    if (newTurns.length === 0) {
      warn("All turns already captured — nothing to do");
      process.exit(0);
    }
  }
  captureTurns = cfg.captureAssistantTurns
    ? newTurns
    : newTurns.filter(turn => turn.role === "user");
  ok(`Turns selected for capture: ${captureTurns.length}`);
  if (!cfg.captureAssistantTurns) {
    dim(`  Assistant turns skipped: ${newTurns.length - captureTurns.length}`);
  }
  if (captureTurns.length === 0) {
    warn("No user turns in the new incremental slice — nothing will be sent");
    if (!skipIncremental) {
      await saveState(sessionId, { capturedTurnCount: allTurns.length });
      dim("  State updated (assistant-only turns marked as seen)");
    }
    process.exit(0);
  }

  // ── Stage 4: shouldCapture decisions ──
  header("shouldCapture Decisions");
  for (let i = 0; i < captureTurns.length; i++) {
    const turn = captureTurns[i];
    const decision = shouldCapture(turn.text);
    const captureLabel = decision.capture
      ? `${C.green}true${C.reset}`
      : `${C.dim}false${C.reset}`;
    console.log(`  [${i}] ${C.dim}(${turn.role})${C.reset} capture=${captureLabel}  reason=${decision.reason}`);
    dim(`       ${preview(decision.text || turn.text, 100)}`);
  }

  // Build combined text from capturable turns (same logic as auto-capture.mjs)
  const turnText = captureTurns.map(t => `[${t.role}]: ${t.text}`).join("\n");
  const combinedDecision = shouldCapture(turnText);

  header("Combined Capture Decision");
  if (combinedDecision.capture) {
    ok(`capture=true  reason=${combinedDecision.reason}`);
  } else {
    fail(`capture=false  reason=${combinedDecision.reason}`);
    // Save state even on skip so next run doesn't re-examine these turns
    if (!skipIncremental) {
      await saveState(sessionId, { capturedTurnCount: allTurns.length });
      dim("  State updated (turns marked as seen)");
    }
    console.log("\nPipeline stopped — combined text did not pass shouldCapture.");
    process.exit(0);
  }

  // ── Stage 5: Text sent to OpenViking ──
  header("Text Sent to OpenViking");
  console.log(preview(combinedDecision.text, 500));
  dim(`\n  (total length: ${combinedDecision.text.length} chars)`);

  // ── Stage 6 + 7: Session pipeline + extracted memories ──
  const result = await captureToOpenViking(combinedDecision.text);

  // Update state
  if (!skipIncremental) {
    await saveState(sessionId, { capturedTurnCount: allTurns.length });
    dim("\nState updated (turns marked as captured)");
  }

  // Final summary
  header("Summary");
  if (result.ok) {
    ok(`Pipeline completed — ${result.count} memor${result.count === 1 ? "y" : "ies"} extracted from ${captureTurns.length} captured turn${captureTurns.length === 1 ? "" : "s"}`);
  } else {
    fail(`Pipeline failed: ${result.reason}`);
    process.exit(1);
  }
}

main().catch(err => {
  fail(`Unhandled error: ${err?.message || err}`);
  if (err?.stack) dim(err.stack);
  process.exit(1);
});
