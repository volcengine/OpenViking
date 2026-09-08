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
 * (MEMORY_TRIGGERS / extractNewTurnMessages).
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { extractCaptureTurns, parseTranscript, sanitizeCapturedText } from "./cc-transcript.mjs";
import {
  commitSession,
  deriveOvSessionId,
  enqueuePendingDirectly,
  getSession,
  isRetryableFailure,
  makeFetchJSON,
} from "./lib/ov-session.mjs";
import { maybeDetach, readHookStdin } from "./lib/async-writer.mjs";
import { readJsonState, writeJsonState } from "./lib/state.mjs";
import { getEffectivePeerId } from "./lib/workspace-peer.mjs";
import { runHookStage } from "./shared/agent-hook-runtime.mjs";
import { sendSessionMessages } from "./shared/batch-send.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const baseCfg = loadConfig();
const { log, logError } = createLogger("auto-capture");
const fetchJSON = makeFetchJSON(baseCfg, "captureTimeoutMs");

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
    return { capturedTurnCount: 0, ...JSON.parse(data) };
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

async function reconcileKnownFailedCapture(cfg, sessionId, ovSessionId, state) {
  if (
    state.capturedTurnCount <= 0
    || cfg.captureMode !== "semantic"
    || !cfg.captureAssistantTurns
  ) {
    return;
  }

  const lastCapture = readJsonState("last-capture.json");
  if (
    lastCapture?.cc_session_id !== sessionId
    || lastCapture?.ov_session_id !== ovSessionId
    || Number(lastCapture.turns_failed || 0) <= 0
    || Number(lastCapture.turns_captured || 0) > 0
    || Number(lastCapture.turns_queued || 0) > 0
  ) {
    return;
  }

  const res = await fetchJSON(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}`);
  if (!res.ok && res.status !== 404) {
    log("capture_state_verification_deferred", {
      sessionId,
      ovSessionId,
      status: res.status || 0,
    });
    return;
  }

  const meta = res.ok ? (res.result || {}) : {};
  const hasDurableCapture = Number(meta.message_count || 0) > 0
    || Number(meta.total_message_count || 0) > 0
    || Number(meta.commit_count || 0) > 0;
  if (!hasDurableCapture) {
    log("capture_state_rewound", {
      sessionId,
      ovSessionId,
      previousCapturedTurnCount: state.capturedTurnCount,
      status: res.status || 200,
    });
    state.capturedTurnCount = 0;
    await saveState(sessionId, state);
  }
}

// Keyword-mode gate (ported from openclaw-plugin/text-utils.ts).
const MEMORY_TRIGGERS = [
  /remember|preference|prefer|important|decision|decided|always|never/i,
  /记住|偏好|喜欢|喜爱|崇拜|讨厌|害怕|重要|决定|总是|永远|优先|习惯|爱好|擅长|最爱|不喜欢/i,
  /[\w.-]+@[\w.-]+\.\w+/,
  /\+\d{10,}/,
  /(?:我|my)\s*(?:是|叫|名字|name|住在|live|来自|from|生日|birthday|电话|phone|邮箱|email)/i,
  /(?:我|i)\s*(?:喜欢|崇拜|讨厌|害怕|擅长|不会|爱|恨|想要|需要|希望|觉得|认为|相信)/i,
  /(?:favorite|favourite|love|hate|enjoy|dislike|admire|idol|fan of)/i,
];

function formatTurnsAsText(turns) {
  const lines = [];
  for (const t of turns) {
    const toolNames = t.parts
      .filter((p) => p.type === "tool" && p.tool_status === "running" && p.tool_name)
      .map((p) => p.tool_name);
    if (t.role === "assistant" && toolNames.length > 0) {
      const uniq = Array.from(new Set(toolNames)).join(", ");
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

// Strip host- and plugin-injected blocks from text parts (tool parts pass
// through), and drop parts that become empty. Per text part, so tool I/O is
// never collapsed.
function sanitizePartsForSend(parts) {
  const out = [];
  for (const p of parts || []) {
    if (p.type === "text") {
      const t = sanitizeCapturedText(p.text);
      if (t) out.push({ type: "text", text: t });
    } else {
      out.push(p);
    }
  }
  return out;
}

async function pushTurnsToOv(ovSessionId, turns, peerId = "") {
  const payloads = [];
  for (const turn of turns) {
    // Send structured parts: tool calls/results are dedicated `tool` parts, not
    // inlined into content, so the server can process them separately.
    const parts = sanitizePartsForSend(turn.parts);
    if (parts.length === 0) continue;

    const payload = { role: turn.role, parts };
    if (peerId) payload.peer_id = peerId;
    payloads.push(payload);
  }
  const res = await sendSessionMessages(fetchJSON, ovSessionId, payloads, {
    enqueueOnRetryable: true,
  });
  return {
    ok: res.sent,
    queued: res.queued,
    failed: res.failed,
    enqueueFailed: res.enqueueFailed,
    lastError: res.lastError,
  };
}

async function enqueueTurnsToPending(ovSessionId, turns, peerId = "") {
  let queued = 0;
  let failed = 0;
  for (const turn of turns) {
    const parts = sanitizePartsForSend(turn.parts);
    if (parts.length === 0) continue;

    const payload = { role: turn.role, parts };
    if (peerId) payload.peer_id = peerId;
    const res = await enqueuePendingDirectly("addMessage", ovSessionId, payload);
    if (res.ok) queued++;
    else failed++;
  }
  return { ok: 0, queued, failed, enqueueFailed: failed };
}

function isMissingSessionState(error) {
  const code = String(error?.code || "").toUpperCase();
  return code === "NOT_FOUND";
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main({ cfg, input, cwd }) {
  const transcriptPath = input.transcript_path;
  const sessionId = input.session_id || "unknown";

  const ovSessionId = sessionId !== "unknown" ? deriveOvSessionId(sessionId) : null;
  const effectivePeer = getEffectivePeerId(cfg, { sessionId, cwd });
  log("start", { sessionId, ovSessionId, transcriptPath, peerSource: effectivePeer.source });

  if (!transcriptPath || !ovSessionId) {
    log("skip", { stage: "input_check", reason: "no transcript_path or session_id" });
    return;
  }

  let transcriptContent;
  try {
    transcriptContent = await readFile(transcriptPath, "utf-8");
  } catch (err) {
    logError("transcript_read", err);
    return;
  }

  if (!transcriptContent.trim()) {
    log("skip", { stage: "transcript_read", reason: "empty transcript" });
    return;
  }

  const messages = parseTranscript(transcriptContent);
  const allTurns = extractCaptureTurns(messages, cfg);
  if (allTurns.length === 0) {
    log("skip", { stage: "transcript_parse", reason: "no user/assistant turns found" });
    return;
  }

  const state = await loadState(sessionId);
  await reconcileKnownFailedCapture(cfg, sessionId, ovSessionId, state);
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
    return;
  }

  if (captureTurns.length === 0) {
    await saveState(sessionId, {
      ...state,
      capturedTurnCount: allTurns.length,
    });
    log("state_update", { newCapturedTurnCount: allTurns.length, reason: "assistant_only_increment" });
    return;
  }

  // Batch-level capture decision. A per-message filter (length bounds,
  // command/punctuation/question-only) misfires on a multi-turn batch
  // concatenated by formatTurnsAsText():
  //   - tool I/O inlining easily pushes combined text over captureMaxLength → entire
  //     batch silently dropped + state advanced → permanent data loss
  //   - JSON-shaped tool I/O can match a punctuation-only rule → non_content drop
  //   - a leading `/cmd` user turn flips the whole batch to `command` → drop
  //   - a question-shaped user turn ("why?") tags the whole batch as question_only
  // For batches we only need: skip empty batches, and (keyword mode) require *some*
  // user turn to carry a trigger phrase. Per-turn substance is already bounded by
  // captureToolMaxChars during harvest.
  const combined = formatTurnsAsText(captureTurns);
  if (!sanitizeCapturedText(combined)) {
    log("skip", { stage: "batch_empty" });
    await saveState(sessionId, {
      ...state,
      capturedTurnCount: allTurns.length,
    });
    return;
  }

  if (cfg.captureMode === "keyword") {
    const hasTrigger = captureTurns.some(
      (t) =>
        t.role === "user" &&
        MEMORY_TRIGGERS.some((re) => re.test(sanitizeCapturedText(t.text))),
    );
    if (!hasTrigger) {
      log("skip", { stage: "keyword_mode_no_trigger", turns: captureTurns.length });
      await saveState(sessionId, {
        ...state,
        capturedTurnCount: allTurns.length,
      });
      return;
    }
  }

  log("should_capture", {
    capture: true,
    reason: cfg.captureMode === "keyword" ? "keyword_trigger_matched" : "semantic",
    combinedLength: combined.length,
  });

  const health = await fetchJSON("/health");
  let result;
  if (health.ok) {
    result = await pushTurnsToOv(ovSessionId, captureTurns, effectivePeer.peerId);
  } else if (isRetryableFailure(health)) {
    logError("health_check", "server unreachable or unhealthy; enqueuing capture");
    result = await enqueueTurnsToPending(ovSessionId, captureTurns, effectivePeer.peerId);
    log("push_turns", {
      ovSessionId,
      ok: result.ok,
      queued: result.queued,
      failed: result.failed,
    });
    if (result.failed > 0) {
      logError("pending_enqueue", "some turns failed to enqueue; state not advanced");
      return;
    }
    await saveState(sessionId, {
      ...state,
      capturedTurnCount: allTurns.length,
    });
    log("state_update", { newCapturedTurnCount: allTurns.length, reason: "pending_queued" });
    writeJsonState("last-capture.json", {
      turns_captured: 0,
      turns_queued: result.queued,
      turns_failed: 0,
      pending_tokens: 0,
      commit_threshold: cfg.commitTokenThreshold,
      committed: false,
      commit_count: 0,
      total_message_count: 0,
      ov_session_id: ovSessionId,
      cc_session_id: sessionId,
    });
    return result.queued > 0 ? `queued ${result.queued} turns to pending queue` : undefined;
  } else {
    logError("health_check", `non-retryable status ${health.status || "unknown"}`);
    return;
  }
  log("push_turns", {
    ovSessionId,
    ok: result.ok,
    queued: result.queued,
    failed: result.failed,
    enqueueFailed: result.enqueueFailed,
  });

  if (result.enqueueFailed > 0 || (
    result.failed > 0
    && result.ok === 0
    && result.queued === 0
    && isMissingSessionState(result.lastError)
  )) {
    logError(
      "capture_write",
      "some turns were neither sent nor queued; state not advanced",
    );
    writeJsonState("last-capture.json", {
      turns_captured: result.ok,
      turns_queued: result.queued,
      turns_failed: result.failed + result.enqueueFailed,
      pending_tokens: 0,
      commit_threshold: cfg.commitTokenThreshold,
      committed: false,
      commit_count: 0,
      total_message_count: 0,
      ov_session_id: ovSessionId,
      cc_session_id: sessionId,
    });
    return;
  }

  // A missing live-message file is recoverable after the server is fixed, so
  // retain the cursor for that failure. Other non-retryable 4xx responses keep
  // the historical terminal behavior instead of retrying invalid payloads.
  await saveState(sessionId, {
    ...state,
    capturedTurnCount: allTurns.length,
  });
  log("state_update", { newCapturedTurnCount: allTurns.length });

  // Client-driven commit (ported from openclaw-plugin/context-engine.ts:afterTurn).
  // OV's Session._auto_commit_threshold is not consumed by addMessage, so we
  // poll pending_tokens ourselves and commit when the threshold is crossed.
  let committed = false;
  let commitTraceId = "";
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
      const commitRes = await commitSession(fetchJSON, ovSessionId, {
        keep_recent_count: cfg.commitKeepRecentCount,
      });
      committed = commitRes.ok;
      commitTraceId = commitRes.traceId || commitRes.result?.trace_id || "";
      if (committed) commitCount += 1;
      log("commit", {
        ovSessionId,
        ok: commitRes.ok,
        trace_id: commitTraceId || undefined,
        pending: pendingTokens,
        keepRecentCount: cfg.commitKeepRecentCount,
      });
    }
  }

  // Snapshot for the statusline. Lives across sessions; statusline reads it
  // alongside last-recall.json to show pending/committed counts. commit_count
  // is the running total of archives this session has produced — distinct
  // from `committed` (which is just whether THIS turn triggered a commit).
  writeJsonState("last-capture.json", {
    turns_captured: result.ok,
    turns_queued: result.queued,
    turns_failed: result.failed,
    pending_tokens: pendingTokens,
    commit_threshold: cfg.commitTokenThreshold,
    committed,
    commit_count: commitCount,
    total_message_count: totalMessageCount,
    ov_session_id: ovSessionId,
    cc_session_id: sessionId,
  });

  // Cross-session daily counter — number of archives produced today across
  // all CC sessions. Cheap proxy for "how much OV digested today" without
  // hitting the server again. Resets on date rollover.
  if (committed) {
    const today = new Date().toISOString().slice(0, 10); // YYYY-MM-DD, UTC
    const prior = readJsonState("daily-stats.json") || {};
    const archives = prior.date === today ? Number(prior.archives || 0) + 1 : 1;
    writeJsonState("daily-stats.json", { date: today, archives });
  }

  if (result.ok === 0) return undefined;
  return `captured ${result.ok} turns to ov session ${ovSessionId}`
    + (committed
      ? ` (committed${commitTraceId ? `; trace_id=${commitTraceId}` : ""})`
      : "");
}

async function start() {
  // Write-path hook: gated by autoCapture so that disabling capture also stops
  // the transcript push. This runs against the hook's own directory, before the
  // payload names the session's.
  if (!baseCfg.autoCapture) {
    log("skip", { stage: "init", reason: "disabled" });
    approve();
    return;
  }

  // Async write path: parent detaches and returns, worker continues below.
  if (await maybeDetach(baseCfg, { approve })) return;

  await runHookStage({
    loadConfig,
    input: { read: readHookStdin },
    gates: { enabled: (cfg) => cfg.autoCapture },
    envelope: approve,
    onSkip: (reason) => log("skip", { stage: "init", reason }),
  }, main);
}

start().catch((err) => { logError("uncaught", err); approve(); });
