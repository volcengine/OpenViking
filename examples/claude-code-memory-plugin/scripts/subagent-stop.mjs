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
 * OV agent identity is overridden per-call via X-OpenViking-Agent header
 * (e.g. "claude-code_general-purpose") so memories segregate by subagent
 * type in viking://agent/<type>/memories/.
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

function stateFile(agentId) {
  const safe = String(agentId).replace(/[^a-zA-Z0-9_-]/g, "_");
  return join(STATE_DIR, `${safe}.json`);
}

async function loadState(agentId) {
  try {
    const data = await readFile(stateFile(agentId), "utf-8");
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

/**
 * Tier-1 parts extraction — shared shape with auto-capture.mjs.
 * Kept inline here so SubagentStop does not import auto-capture's globals.
 * Inlines tool_use input verbatim; tool_result content is dropped by default
 * (TOOL_RESULT_MAX_CHARS = 0) and retained only if explicitly enabled.
 */
function extractTurns(messages) {
  const turns = [];
  for (const msg of messages) {
    if (!msg || typeof msg !== "object") continue;
    let role = msg.role;
    let text = "";
    const toolNames = [];

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

async function pushTurns(ovSessionId, ovAgentId, turns) {
  // Per-call agent override: we mint a new fetchJSON whose cfg has agentId
  // replaced so X-OpenViking-Agent reflects the subagent type.
  const subCfg = { ...cfg, agentId: ovAgentId };
  const fetchJSON = makeFetchJSON(subCfg);
  let ok = 0;
  let failed = 0;
  for (const turn of turns) {
    // Tool input / result already inlined as `[tool: NAME]` / `[tool result]` during harvest.
    const content = turn.text;
    if (!content) continue;
    const res = await addMessage(fetchJSON, ovSessionId, { role: turn.role, content });
    if (res.ok) ok++;
    else failed++;
  }
  // Commit once at the end — subagents are short-lived, no point tracking
  // the threshold. This also makes their context available to the parent
  // via viking://agent/<type> immediately.
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
  const agentId = input.agent_id;
  const transcriptPath = input.agent_transcript_path;
  const agentType = input.agent_type || "subagent";

  if (!sessionId || !agentId || !transcriptPath) {
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
  const state = await loadState(agentId);
  const ovSessionId = state?.ovSessionId || deriveOvSessionId(sessionId, `agent:${agentId}`);
  const ovAgentId = state?.ovAgentId || `${cfg.agentId || "claude-code"}_${agentType}`;

  const fetchJSON = makeFetchJSON({ ...cfg, agentId: ovAgentId });
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
    agentId,
    ovSessionId,
    ovAgentId,
    totalTurns: turns.length,
  });

  if (turns.length === 0) {
    await unlink(stateFile(agentId)).catch(() => {});
    approve();
    return;
  }

  const result = await pushTurns(ovSessionId, ovAgentId, turns);
  log("push_turns", { ovSessionId, ovAgentId, ...result });

  await unlink(stateFile(agentId)).catch(() => {});
  approve();
}

main().catch((err) => { logError("uncaught", err); approve(); });
