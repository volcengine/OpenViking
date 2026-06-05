#!/usr/bin/env node

/**
 * SubagentStop Hook for Claude Code.
 *
 * Fires when a subagent finishes. Platform input shape:
 *   { session_id, agent_id, agent_type, agent_transcript_path, ... }
 *
 * Regular in-subagent hooks never fire, so this is the only place we can
 * capture the subagent's turns. We read its transcript jsonl, extract
 * tier-1 parts (text + tool-use name list), and push to the isolated
 * ovSessionId we created in subagent-start.mjs. An immediate commit runs
 * so the subagent's context is archived before the parent continues.
 *
 * Each subagent is written to a distinct OpenViking session derived from the
 * parent session id and Claude's subagent id.
 */

import { readFile, unlink } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import {
  addMessage,
  commitSession,
  deriveOvSessionId,
  isBypassed,
  makeFetchJSON,
} from "./lib/ov-session.mjs";
import { maybeDetach, readHookStdin } from "./lib/async-writer.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const cfg = loadConfig();
const { log, logError } = createLogger("subagent-stop");

const STATE_DIR = join(tmpdir(), "openviking-cc-subagent-state");

function approve() {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
}

function stateFile(subagentId) {
  const safe = String(subagentId).replace(/[^a-zA-Z0-9_-]/g, "_");
  return join(STATE_DIR, `${safe}.json`);
}

function peerIdFromSubagent(subagentId) {
  if (cfg.peerId) return cfg.peerId;
  return String(subagentId || "").replace(/[^A-Za-z0-9._-]/g, "-") || null;
}

async function loadState(subagentId) {
  try {
    const data = await readFile(stateFile(subagentId), "utf-8");
    return JSON.parse(data);
  } catch {
    return null;
  }
}

function parseTranscript(content) {
  const lines = content.split("\n").filter(l => l.trim());
  const out = [];
  for (const line of lines) {
    try { out.push(JSON.parse(line)); } catch { /* skip */ }
  }
  return out;
}

// Tool result (output) retention. 0 = drop tool_result entirely; >0 = keep, truncated.
// Default 0 — see auto-capture.mjs for rationale. Mirrors auto-capture.mjs.
const TOOL_RESULT_MAX_CHARS = 0;

function formatToolInput(value) {
  // Tool inputs are agent-authored; we keep them verbatim.
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function truncateToolResult(s) {
  if (TOOL_RESULT_MAX_CHARS <= 0) return null; // drop
  if (typeof s !== "string") s = String(s ?? "");
  if (s.length <= TOOL_RESULT_MAX_CHARS) return s;
  return (
    s.slice(0, TOOL_RESULT_MAX_CHARS) +
    `\n... [truncated, ${s.length - TOOL_RESULT_MAX_CHARS} more chars]`
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

// Structured parts (parts-mode capture) — mirrors auto-capture.mjs. Tool calls /
// results become dedicated `tool` parts instead of being inlined into content.
const TOOL_OUTPUT_PART_MAX_CHARS = 2000;

function truncateToolOutput(s) {
  if (typeof s !== "string") s = String(s ?? "");
  if (s.length <= TOOL_OUTPUT_PART_MAX_CHARS) return s;
  return (
    s.slice(0, TOOL_OUTPUT_PART_MAX_CHARS) +
    `\n... [truncated, ${s.length - TOOL_OUTPUT_PART_MAX_CHARS} more chars]`
  );
}

function collectToolNamesById(messages) {
  const map = {};
  for (const msg of messages) {
    const content = msg?.content ?? msg?.message?.content;
    if (!Array.isArray(content)) continue;
    for (const block of content) {
      if (
        block?.type === "tool_use" &&
        typeof block.id === "string" &&
        typeof block.name === "string"
      ) {
        map[block.id] = block.name;
      }
    }
  }
  return map;
}

function buildParts(content, toolNameById) {
  const out = [];
  if (typeof content === "string") {
    if (content.trim()) out.push({ type: "text", text: content });
    return out;
  }
  if (!Array.isArray(content)) return out;
  for (const block of content) {
    if (!block || typeof block !== "object") continue;
    if (block.type === "text" && typeof block.text === "string") {
      if (block.text.trim()) out.push({ type: "text", text: block.text });
    } else if (block.type === "tool_use" && typeof block.name === "string") {
      out.push({
        type: "tool",
        tool_id: typeof block.id === "string" ? block.id : undefined,
        tool_name: block.name,
        tool_input:
          block.input && typeof block.input === "object" ? block.input : undefined,
        tool_status: "running",
      });
    } else if (block.type === "tool_result") {
      const id = typeof block.tool_use_id === "string" ? block.tool_use_id : undefined;
      out.push({
        type: "tool",
        tool_id: id,
        tool_name: id ? toolNameById[id] : undefined,
        tool_output: truncateToolOutput(extractToolResultText(block.content)),
        tool_status: block.is_error ? "error" : "completed",
      });
    }
  }
  return out;
}

/**
 * Tier-1 parts extraction — shared shape with auto-capture.mjs.
 * Kept inline here so SubagentStop does not import auto-capture's globals.
 * Inlines tool_use input verbatim; tool_result content is dropped by default
 * (TOOL_RESULT_MAX_CHARS = 0) and retained only if explicitly enabled.
 */
function extractTurns(messages) {
  const toolNameById = collectToolNamesById(messages);
  const turns = [];
  for (const msg of messages) {
    if (!msg || typeof msg !== "object") continue;
    let role = msg.role;
    let text = "";
    const toolNames = [];
    let parts = [];

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
            parts.push(`[tool: ${block.name}]\n${formatToolInput(block.input)}`);
          } else if (block.type === "tool_result") {
            const resultText = extractToolResultText(block.content);
            const truncated = resultText ? truncateToolResult(resultText) : null;
            if (truncated) {
              parts.push(`[tool result]\n${truncated}`);
            }
          }
        }
        text = parts.join("\n\n");
      }
    };

    let rawContent;
    if (msg.content !== undefined) {
      rawContent = msg.content;
    } else if (typeof msg.message === "object" && msg.message) {
      role = msg.message.role || role;
      rawContent = msg.message.content;
    }
    harvestContent(rawContent);
    parts = buildParts(rawContent, toolNameById);

    if (role !== "user" && role !== "assistant") continue;
    if (parts.length === 0) continue;
    turns.push({ role, text: text.trim(), toolNames, parts });
  }
  return turns;
}

async function pushTurns(ovSessionId, turns, peerId = null) {
  const fetchJSON = makeFetchJSON(cfg);
  let ok = 0;
  let failed = 0;
  for (const turn of turns) {
    // Send structured parts: tool calls/results are dedicated `tool` parts, not
    // inlined into content, so the server can process them separately.
    const parts = (turn.parts || []).filter(
      (p) => p.type !== "text" || (p.text && p.text.trim()),
    );
    if (parts.length === 0) continue;
    const payload = { role: turn.role, parts };
    if (peerId) payload.peer_id = peerId;
    const res = await addMessage(fetchJSON, ovSessionId, payload);
    if (res.ok) ok++;
    else failed++;
  }
  // Commit once at the end; subagents are short-lived, so threshold tracking
  // adds little value.
  let committed = false;
  if (ok > 0) {
    const commitRes = await commitSession(fetchJSON, ovSessionId);
    committed = commitRes.ok;
  }
  return { ok, failed, committed };
}

async function main() {
  // Write-path hook: gated by autoCapture so that disabling capture also
  // suppresses the subagent transcript push + commit.
  if (!cfg.autoCapture) {
    log("skip", { reason: "autoCapture disabled" });
    approve();
    return;
  }

  if (await maybeDetach(cfg, { approve })) return;

  let input = {};
  try {
    input = JSON.parse((await readHookStdin()) || "{}");
  } catch { /* best effort */ }

  const sessionId = input.session_id;
  const cwd = input.cwd;
  const subagentId = input.agent_id;
  const transcriptPath = input.agent_transcript_path;

  if (!sessionId || !subagentId || !transcriptPath) {
    log("skip", { reason: "missing required input fields" });
    approve();
    return;
  }

  if (isBypassed(cfg, { sessionId, cwd })) {
    log("skip", { reason: "bypass_session_pattern" });
    approve();
    return;
  }

  // Prefer state from SubagentStart (may carry ovSessionId from config snapshot);
  // fall back to live derivation if state file is missing.
  const state = await loadState(subagentId);
  const ovSessionId = state?.ovSessionId || deriveOvSessionId(sessionId, `subagent:${subagentId}`);

  const fetchJSON = makeFetchJSON(cfg);
  const health = await fetchJSON("/health");
  if (!health.ok) {
    logError("health_check", "server unreachable");
    approve();
    return;
  }

  let transcript;
  try {
    transcript = await readFile(transcriptPath, "utf-8");
  } catch (err) {
    logError("transcript_read", err);
    approve();
    return;
  }

  const messages = parseTranscript(transcript);
  const turns = extractTurns(messages);
  log("transcript_parse", {
    subagentId,
    ovSessionId,
    totalTurns: turns.length,
  });

  if (turns.length === 0) {
    await unlink(stateFile(subagentId)).catch(() => {});
    approve();
    return;
  }

  const peerId = peerIdFromSubagent(subagentId);
  const result = await pushTurns(ovSessionId, turns, peerId);
  log("push_turns", { ovSessionId, ...result });

  await unlink(stateFile(subagentId)).catch(() => {});
  approve();
}

main().catch((err) => { logError("uncaught", err); approve(); });
