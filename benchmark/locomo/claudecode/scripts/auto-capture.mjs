#!/usr/bin/env node

/**
 * LoCoMo benchmark companion to the plugin's auto-capture.mjs.
 *
 * Difference from upstream:
 *   pushTurnsToOv() extracts the LoCoMo session_time stamp from the user
 *   prompt (e.g. "[group chat conversation: 4:02 pm on 12 February, 2023]"
 *   or "=== session_3 (...) ===") and passes a per-message `created_at`
 *   to OV addMessage, mirroring the SDK ingest path (import_to_ov.py).
 *
 * Why: the OV server derives event archive dates from message.created_at.
 * Without this patch, e2e auto-captured LoCoMo conversations all stamp
 * under the ingest day, collapsing the temporal dimension that semantic
 * search relies on.
 *
 * Behaviour outside LoCoMo prompts is unchanged: regex miss → omit
 * created_at → server falls back to now() (upstream behaviour).
 *
 * The upstream plugin's lib is loaded dynamically from
 * $OPENVIKING_PLUGIN_DIR so this script lives outside the plugin tree and
 * the plugin source is untouched.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { pathToFileURL } from "node:url";

const pluginDir = process.env.OPENVIKING_PLUGIN_DIR;
if (!pluginDir) {
  process.stderr.write("auto-capture: OPENVIKING_PLUGIN_DIR not set\n");
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}
const PLUGIN_SCRIPTS = pathToFileURL(`${pluginDir}/scripts`).href;

const { isPluginEnabled, loadConfig } = await import(`${PLUGIN_SCRIPTS}/config.mjs`);
const { createLogger } = await import(`${PLUGIN_SCRIPTS}/debug-log.mjs`);
const {
  addMessage,
  commitSession,
  deriveOvSessionId,
  getSession,
  isBypassed,
  makeFetchJSON,
} = await import(`${PLUGIN_SCRIPTS}/lib/ov-session.mjs`);
const { maybeDetach, readHookStdin } = await import(`${PLUGIN_SCRIPTS}/lib/async-writer.mjs`);
const { readJsonState, writeJsonState } = await import(`${PLUGIN_SCRIPTS}/lib/state.mjs`);

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const cfg = loadConfig();
const { log, logError } = createLogger("auto-capture-locomo-e2e");
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
// Text processing (verbatim from upstream)
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
const CJK_CHAR_RE = /[぀-ヿ㐀-鿿豈-﫿가-힯]/;
const QUESTION_ONLY_RE = /^(who|what|when|where|why|how|is|are|does|did|can|could|would|should|may|might|will|谁|什么|何|哪|为什么|怎么|如何|是|会|能|能否)\b.{0,200}[?？]$/i;

function stripInjectedBlocks(text) {
  return text
    .replace(RELEVANT_MEMORIES_BLOCK_RE, "")
    .replace(OPENVIKING_CTX_BLOCK_RE, "")
    .replace(SYSTEM_REMINDER_BLOCK_RE, "")
    .replace(SUBAGENT_CONTEXT_LINE_RE, "")
    .replace(/\x00/g, "");
}

// CC's `claude -p --resume <id>` injects synthetic bridging turns:
//   user: "Continue from where you left off."
//   assistant: "No response requested."
// These are not real conversation content and contaminate per-message
// ingest if passed to OV — filter them out here.
const CC_RESUME_BRIDGE_RE = /^(continue from where you left off\.?|no response requested\.?)$/i;
function isCcResumeBridge(text) {
  if (!text) return false;
  const trimmed = text.trim();
  if (trimmed.length > 60) return false;
  return CC_RESUME_BRIDGE_RE.test(trimmed);
}

function sanitize(text) {
  return stripInjectedBlocks(text)
    .replace(/\s+/g, " ")
    .trim();
}

// ---------------------------------------------------------------------------
// LoCoMo session_time extraction (the only behavioural delta from upstream)
// ---------------------------------------------------------------------------

const MONTHS = {
  january: 0, february: 1, march: 2, april: 3, may: 4, june: 5,
  july: 6, august: 7, september: 8, october: 9, november: 10, december: 11,
};

// Matches LoCoMo session_X_date_time strings like "7:55 pm on 9 June, 2023"
// whether wrapped in `(...)` (v12 prompt) or `[...]` (v14 group-chat header)
// or appearing bare. Boundaries are loose because LoCoMo prompts vary.
const LOCOMO_TS_RE =
  /(\d{1,2}):(\d{2})\s*([ap]m)\s+on\s+(\d{1,2})\s+([A-Za-z]+),\s*(\d{4})/i;

function parseLocomoStamp(text) {
  const m = text.match(LOCOMO_TS_RE);
  if (!m) return null;
  let h = parseInt(m[1], 10);
  const min = parseInt(m[2], 10);
  const ampm = m[3].toLowerCase();
  if (ampm === "pm" && h < 12) h += 12;
  if (ampm === "am" && h === 12) h = 0;
  const day = parseInt(m[4], 10);
  const mo = MONTHS[m[5].toLowerCase()];
  const year = parseInt(m[6], 10);
  if (mo === undefined) return null;
  // Use UTC to avoid host TZ skewing the day boundary.
  return new Date(Date.UTC(year, mo, day, h, min, 0));
}

function extractBaseDate(turns) {
  for (const t of turns) {
    if (t.role !== "user") continue;
    const d = parseLocomoStamp(t.text || "");
    if (d) return d;
  }
  return null;
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

  return { capture: true, reason: "semantic", text: normalized };
}

// ---------------------------------------------------------------------------
// Transcript parsing (verbatim from upstream)
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
// Persistent-session capture (LoCoMo-aware created_at injection)
// ---------------------------------------------------------------------------

async function pushTurnsToOv(ovSessionId, turns, baseDate) {
  let ok = 0;
  let failed = 0;
  let idx = 0;
  let bridgesSkipped = 0;
  for (const turn of turns) {
    const content = stripInjectedBlocks(turn.text).trim();
    if (!content) continue;
    if (isCcResumeBridge(content)) { bridgesSkipped++; continue; }

    const payload = { role: turn.role, content };
    if (baseDate) {
      const ts = new Date(baseDate.getTime() + idx * 1000).toISOString();
      payload.created_at = ts;
    }

    const res = await addMessage(fetchJSON, ovSessionId, payload);
    if (res.ok) { ok++; idx++; }
    else failed++;
  }
  return { ok, failed, bridgesSkipped };
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

  // LoCoMo timestamp: scan ALL captured turns (not just newTurns) so that on
  // an incremental Stop firing, the date — which lives in the very first user
  // turn header — is still recoverable. In our ingest path each `claude -p`
  // is a fresh CC session with capturedTurnCount=0, so allTurns == newTurns
  // here in practice; the fallback exists for robustness.
  const baseDate = extractBaseDate(allTurns);

  log("transcript_parse", {
    totalTurns: allTurns.length,
    previouslyCaptured: state.capturedTurnCount,
    newTurns: newTurns.length,
    captureTurns: captureTurns.length,
    assistantTurnsSkipped: newTurns.length - captureTurns.length,
    locomoBaseDate: baseDate ? baseDate.toISOString() : null,
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

  const result = await pushTurnsToOv(ovSessionId, captureTurns, baseDate);
  log("push_turns", {
    ovSessionId,
    ok: result.ok,
    failed: result.failed,
    locomoBaseDate: baseDate ? baseDate.toISOString() : null,
  });

  await saveState(sessionId, { capturedTurnCount: allTurns.length });
  log("state_update", { newCapturedTurnCount: allTurns.length });

  let committed = false;
  let pendingTokens = 0;
  let commitCount = 0;
  let totalMessageCount = 0;
  if (result.ok > 0) {
    const meta = await getSession(fetchJSON, ovSessionId);
    pendingTokens = Number(meta?.pending_tokens || 0);
    commitCount = Number(meta?.commit_count || 0);
    totalMessageCount = Number(meta?.total_message_count || 0);
    log("pending_tokens", { ovSessionId, pending: pendingTokens, threshold: cfg.commitTokenThreshold });
    if (pendingTokens >= cfg.commitTokenThreshold) {
      const commitRes = await commitSession(fetchJSON, ovSessionId);
      committed = commitRes.ok;
      if (committed) commitCount += 1;
      log("commit", { ovSessionId, ok: commitRes.ok, pending: pendingTokens });
    }
  }

  writeJsonState("last-capture.json", {
    turns_captured: result.ok,
    turns_failed: result.failed,
    pending_tokens: pendingTokens,
    commit_threshold: cfg.commitTokenThreshold,
    committed,
    commit_count: commitCount,
    total_message_count: totalMessageCount,
    ov_session_id: ovSessionId,
    cc_session_id: sessionId,
    locomo_base_date: baseDate ? baseDate.toISOString() : null,
  });

  if (committed) {
    const today = new Date().toISOString().slice(0, 10);
    const prior = readJsonState("daily-stats.json") || {};
    const archives = prior.date === today ? Number(prior.archives || 0) + 1 : 1;
    writeJsonState("daily-stats.json", { date: today, archives });
  }

  if (result.ok > 0) {
    approve(
      `captured ${result.ok} turns to ov session ${ovSessionId}` +
      (committed ? " (committed)" : "") +
      (baseDate ? ` [locomo:${baseDate.toISOString().slice(0,10)}]` : ""),
    );
  } else {
    approve();
  }
}

main().catch((err) => { logError("uncaught", err); approve(); });
