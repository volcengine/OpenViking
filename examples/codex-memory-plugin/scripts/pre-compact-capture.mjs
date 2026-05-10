#!/usr/bin/env node

/**
 * Pre-Compact Hook Script for Codex.
 *
 * Triggered by the PreCompact hook. Codex passes:
 *   { session_id, transcript_path, cwd, hook_event_name: "PreCompact",
 *     model, trigger: "manual"|"auto", turn_id }
 *
 * Codex is about to summarize / compact the conversation, dropping detail.
 * Before that happens, we open ONE OpenViking session, push every uncaptured
 * turn from the rollout in order, and commit. This produces a structured
 * extraction that survives compaction.
 *
 * PreCompact output schema (codex-rs/hooks/schema/generated/pre-compact.command.output.schema.json)
 * does NOT support `decision`. Valid keys: continue, stopReason, suppressOutput, systemMessage.
 * No-op output is `{}`.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("pre-compact");

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

function stateFilePath(sessionId) {
  const safe = sessionId.replace(/[^a-zA-Z0-9_-]/g, "_");
  return join(STATE_DIR, `${safe}.json`);
}

async function loadState(sessionId) {
  try {
    const data = await readFile(stateFilePath(sessionId), "utf-8");
    return JSON.parse(data);
  } catch {
    return { capturedTurnCount: 0, lastAssistantMessageHash: null, compactedAt: null };
  }
}

async function saveState(sessionId, state) {
  try {
    await mkdir(STATE_DIR, { recursive: true });
    await writeFile(stateFilePath(sessionId), JSON.stringify(state));
  } catch { /* best effort */ }
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
    if (text.trim()) turns.push({ role, text: text.trim() });
  }
  return turns;
}

async function commitFullSession(turns) {
  const created = await fetchJSON("/api/v1/sessions", {
    method: "POST",
    body: JSON.stringify({}),
  });
  if (!created?.session_id) return { ok: false, reason: "session_create_failed" };
  const ovSessionId = created.session_id;

  for (const turn of turns) {
    await fetchJSON(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}/messages`, {
      method: "POST",
      body: JSON.stringify({ role: turn.role, content: turn.text }),
    });
  }

  const commit = await fetchJSON(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}/commit`, {
    method: "POST",
    body: JSON.stringify({}),
  });

  return {
    ok: true,
    ovSessionId,
    extracted: commit?.memories_extracted || null,
    archived: commit?.archived ?? false,
    taskId: commit?.task_id,
    status: commit?.status,
  };
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

  if (!transcriptPath) {
    log("skip", { stage: "input_check", reason: "no transcript_path" });
    noop();
    return;
  }

  let raw;
  try {
    raw = await readFile(transcriptPath, "utf-8");
  } catch (err) {
    logError("transcript_read", err);
    noop();
    return;
  }
  if (!raw.trim()) {
    log("skip", { stage: "transcript_read", reason: "empty" });
    noop();
    return;
  }

  const entries = parseTranscript(raw);
  const allTurns = extractTurns(entries);
  log("transcript_parse", { totalTurns: allTurns.length });

  if (allTurns.length === 0) {
    noop();
    return;
  }

  // Truncate over-long turns rather than dropping them — compaction is a one-shot.
  const trimmed = allTurns.map((turn) => ({
    role: turn.role,
    text: turn.text.length > cfg.captureMaxLength
      ? turn.text.slice(0, cfg.captureMaxLength)
      : turn.text,
  }));

  const result = await commitFullSession(trimmed);
  log("commit_full_session", result);

  // Mark transcript as fully consumed so Stop hook stops re-capturing.
  const state = await loadState(sessionId);
  state.capturedTurnCount = allTurns.length;
  state.compactedAt = Date.now();
  await saveState(sessionId, state);

  if (result.ok) {
    const mem = result.extracted && typeof result.extracted === "object"
      ? Object.values(result.extracted).reduce((a, b) => a + (typeof b === "number" ? b : 0), 0)
      : 0;
    noop(`pre-compact commit: ${trimmed.length} turns sent to OpenViking, ${mem} memory item(s) extracted${result.archived ? " (archived)" : ""}`);
  } else {
    noop();
  }
}

main().catch((err) => { logError("uncaught", err); noop(); });
