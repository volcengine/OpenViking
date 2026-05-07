#!/usr/bin/env node

/**
 * Auto-Capture Hook Script for Claude Code
 *
 * Triggered by Stop hook.
 * Reads transcript_path from stdin → extracts INCREMENTAL new turns since last
 * capture → pushes them to a PERSISTENT per-CC-session OpenViking session.
 *
 * Unlike the previous one-shot model (create→add→extract→delete every Stop),
 * this keeps a stable ovSessionId derived from the CC session_id. OV's own
 * auto_commit_threshold (openviking/session/session.py) drives archive + extract.
 * This preserves cross-turn context for the memory extractor, produces archives
 * naturally, and lets resume / PreCompact / SessionEnd reuse the same session.
 *
 * Incremental tracking: state file per CC session_id records capturedTurnCount.
 *
 * Ported from openclaw-plugin/ context-engine.ts + text-utils.ts
 * (sanitize / MEMORY_TRIGGERS / extractNewTurnMessages).
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import {
  addMessage,
  commitSession,
  deriveOvSessionId,
  getSession,
  isBypassed,
  makeFetchJSON,
} from "./lib/ov-session.mjs";
import { maybeDetach, readHookStdin } from "./lib/async-writer.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const cfg = loadConfig();
const { log, logError } = createLogger("auto-capture");
const fetchJSON = makeFetchJSON(cfg, "captureTimeoutMs");

const STATE_DIR = join(tmpdir(), "openviking-cc-capture-state");

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function approve(msg) {
  const out = { decision: "approve" };
  if (msg) out.systemMessage = msg;
  output(out);
}

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
// Text processing (ported from openclaw-plugin/text-utils.ts)
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
const OPENVIKING_CTX_BLOCK_RE = /<openviking-context>[\s\S]*?<\/openviking-context>/gi;
const SYSTEM_REMINDER_BLOCK_RE = /<system-reminder>[\s\S]*?<\/system-reminder>/gi;
const SUBAGENT_CONTEXT_LINE_RE = /^\[Subagent Context\][^\n]*$/gmi;
const COMMAND_TEXT_RE = /^\/[a-z0-9_-]{1,64}\b/i;
const NON_CONTENT_TEXT_RE = /^[\p{P}\p{S}\s]+$/u;
const CJK_CHAR_RE = /[぀-ヿ㐀-鿿豈-﫿가-힯]/;
// Question-only heuristic (ported from openclaw-plugin/text-utils.ts
// looksLikeQuestionOnlyText). Pure interrogatives rarely yield memories.
const QUESTION_ONLY_RE = /^(who|what|when|where|why|how|is|are|does|did|can|could|would|should|may|might|will|谁|什么|何|哪|为什么|怎么|如何|是|会|能|能否)\b.{0,200}[?？]$/i;

// Strip plugin-injected blocks (auto-recall context, system reminders,
// subagent context, relevant-memories) without collapsing whitespace —
// preserves the user's original formatting (newlines, code blocks) for
// storage in OV. Without this, the auto-recall block we inject this turn
// would be captured back into OV next turn, causing a self-referential
// pollution loop.
function stripInjectedBlocks(text) {
  return text
    .replace(RELEVANT_MEMORIES_BLOCK_RE, "")
    .replace(OPENVIKING_CTX_BLOCK_RE, "")
    .replace(SYSTEM_REMINDER_BLOCK_RE, "")
    .replace(SUBAGENT_CONTEXT_LINE_RE, "")
    .replace(/\x00/g, "");
}

function sanitize(text) {
  return stripInjectedBlocks(text)
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

  if (QUESTION_ONLY_RE.test(normalized)) {
    return { capture: false, reason: "question_only", text: normalized };
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

// Per-block cap for tool input / tool result snippets. Sized to keep most invocations
// (URLs, file paths, search queries, short results) verbatim while bounding worst-case
// blowup when an agent reads a large file or fetches a long page.
const TOOL_BLOCK_MAX_CHARS = 4096;

function truncateForLog(value) {
  let s;
  if (typeof value === "string") {
    s = value;
  } else {
    try {
      s = JSON.stringify(value, null, 2);
    } catch {
      s = String(value);
    }
  }
  if (typeof s !== "string") s = "";
  if (s.length <= TOOL_BLOCK_MAX_CHARS) return s;
  return (
    s.slice(0, TOOL_BLOCK_MAX_CHARS) +
    `\n... [truncated, ${s.length - TOOL_BLOCK_MAX_CHARS} more chars]`
  );
}

function extractToolResultText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((b) => b && b.type === "text" && typeof b.text === "string")
    .map((b) => b.text)
    .join("\n");
}

/**
 * Extract user/assistant turns. Captures plain text, tool_use input (server-side args),
 * and tool_result content (tool output). Tool blocks are inlined into the per-turn text
 * so the OV memory extractor sees "what happened" with substance, not just tool names.
 * Each tool block is truncated to TOOL_BLOCK_MAX_CHARS to bound size.
 */
function extractAllTurns(messages) {
  const turns = [];
  for (const msg of messages) {
    if (!msg || typeof msg !== "object") continue;

    let role = msg.role;
    let text = "";
    let toolNames = [];

    const harvestContent = (content) => {
      if (typeof content === "string") {
        text = content;
      } else if (Array.isArray(content)) {
        const parts = [];
        for (const block of content) {
          if (!block || typeof block !== "object") continue;
          if (block.type === "text" && typeof block.text === "string") {
            parts.push(block.text);
          } else if (block.type === "tool_use" && typeof block.name === "string") {
            toolNames.push(block.name);
            parts.push(`[tool: ${block.name}]\n${truncateForLog(block.input)}`);
          } else if (block.type === "tool_result") {
            const resultText = extractToolResultText(block.content);
            if (resultText) {
              parts.push(`[tool result]\n${truncateForLog(resultText)}`);
            }
          }
        }
        text = parts.join("\n\n");
      }
    };

    if (msg.content !== undefined) {
      harvestContent(msg.content);
    } else if (typeof msg.message === "object" && msg.message) {
      role = msg.message.role || role;
      harvestContent(msg.message.content);
    }

    if (role !== "user" && role !== "assistant") continue;
    if (!text.trim() && toolNames.length === 0) continue;
    turns.push({ role, text: text.trim(), toolNames });
  }
  return turns;
}

function formatTurnsAsText(turns) {
  const lines = [];
  for (const t of turns) {
    if (t.role === "assistant" && t.toolNames.length > 0) {
      const uniq = Array.from(new Set(t.toolNames)).join(", ");
      if (t.text) lines.push(`[assistant]: ${t.text}`);
      lines.push(`[assistant used tools: ${uniq}]`);
    } else if (t.text) {
      lines.push(`[${t.role}]: ${t.text}`);
    }
  }
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Persistent-session capture
// ---------------------------------------------------------------------------

async function pushTurnsToOv(ovSessionId, turns) {
  let ok = 0;
  let failed = 0;
  for (const turn of turns) {
    // Tool input + tool_result are already inlined as `[tool: NAME]` / `[tool result]`
    // blocks during harvesting, so no separate suffix is needed here.
    const content = stripInjectedBlocks(turn.text).trim();
    if (!content) continue;

    const res = await addMessage(fetchJSON, ovSessionId, { role: turn.role, content });
    if (res.ok) ok++;
    else failed++;
  }
  return { ok, failed };
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

  // Async write path: parent detaches and returns, worker continues below.
  if (await maybeDetach(cfg, { approve })) return;

  let input;
  try {
    input = JSON.parse(await readHookStdin());
  } catch {
    log("skip", { stage: "stdin_parse", reason: "invalid input" });
    approve();
    return;
  }

  const transcriptPath = input.transcript_path;
  const sessionId = input.session_id || "unknown";
  const cwd = input.cwd;
  const ovSessionId = sessionId !== "unknown" ? deriveOvSessionId(sessionId) : null;
  log("start", { sessionId, ovSessionId, transcriptPath });

  if (isBypassed(cfg, { sessionId, cwd })) {
    log("skip", { reason: "bypass_session_pattern" });
    approve();
    return;
  }

  const health = await fetchJSON("/health");
  if (!health.ok) {
    logError("health_check", "server unreachable or unhealthy");
    approve();
    return;
  }

  if (!transcriptPath || !ovSessionId) {
    log("skip", { stage: "input_check", reason: "no transcript_path or session_id" });
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

  const messages = parseTranscript(transcriptContent);
  const allTurns = extractAllTurns(messages);
  if (allTurns.length === 0) {
    log("skip", { stage: "transcript_parse", reason: "no user/assistant turns found" });
    approve();
    return;
  }

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

  // Batch-level capture decision. shouldCapture() is designed to evaluate a *single
  // user message* (length bounds, command/punctuation/question-only filters, keyword
  // trigger). Applied to a multi-turn batch concatenated by formatTurnsAsText(), it
  // misfires:
  //   - tool I/O inlining easily pushes combined text over captureMaxLength → entire
  //     batch silently dropped + state advanced → permanent data loss
  //   - JSON-shaped tool I/O can match the punctuation-only regex → non_content drop
  //   - a leading `/cmd` user turn flips the whole batch to `command` → drop
  //   - a question-shaped user turn ("why?") tags the whole batch as question_only
  // For batches we only need: skip empty batches, and (keyword mode) require *some*
  // user turn to carry a trigger phrase. Per-turn substance is already bounded by
  // TOOL_BLOCK_MAX_CHARS during harvest.
  const combined = formatTurnsAsText(captureTurns);
  if (!sanitize(combined)) {
    log("skip", { stage: "batch_empty" });
    await saveState(sessionId, { capturedTurnCount: allTurns.length });
    approve();
    return;
  }

  if (cfg.captureMode === "keyword") {
    const hasTrigger = captureTurns.some(
      (t) =>
        t.role === "user" &&
        MEMORY_TRIGGERS.some((re) => re.test(sanitize(t.text))),
    );
    if (!hasTrigger) {
      log("skip", { stage: "keyword_mode_no_trigger", turns: captureTurns.length });
      await saveState(sessionId, { capturedTurnCount: allTurns.length });
      approve();
      return;
    }
  }

  log("should_capture", {
    capture: true,
    reason: cfg.captureMode === "keyword" ? "keyword_trigger_matched" : "semantic",
    combinedLength: combined.length,
  });

  const result = await pushTurnsToOv(ovSessionId, captureTurns);
  log("push_turns", { ovSessionId, ok: result.ok, failed: result.failed });

  // Advance state regardless of per-turn failures: retrying the same turns on
  // a persistent session would duplicate them. Accepting the loss is cheaper.
  await saveState(sessionId, { capturedTurnCount: allTurns.length });
  log("state_update", { newCapturedTurnCount: allTurns.length });

  // Client-driven commit (ported from openclaw-plugin/context-engine.ts:afterTurn).
  // OV's Session._auto_commit_threshold is not consumed by addMessage, so we
  // poll pending_tokens ourselves and commit when the threshold is crossed.
  let committed = false;
  if (result.ok > 0) {
    const meta = await getSession(fetchJSON, ovSessionId);
    const pending = Number(meta?.pending_tokens || 0);
    log("pending_tokens", { ovSessionId, pending, threshold: cfg.commitTokenThreshold });
    if (pending >= cfg.commitTokenThreshold) {
      const commitRes = await commitSession(fetchJSON, ovSessionId);
      committed = commitRes.ok;
      log("commit", { ovSessionId, ok: commitRes.ok, pending });
    }
  }

  if (result.ok > 0) {
    approve(
      `captured ${result.ok} turns to ov session ${ovSessionId}` +
      (committed ? " (committed)" : ""),
    );
  } else {
    approve();
  }
}

main().catch((err) => { logError("uncaught", err); approve(); });
