#!/usr/bin/env node

/**
 * SubagentStart Hook for Claude Code.
 *
 * Fires when the parent session spawns a subagent via the Agent (Task) tool.
 * Platform input shape (verified via /tmp/hook-test/):
 *   { session_id, agent_id, agent_type, hook_event_name: "SubagentStart" }
 *
 * We do two things:
 *   1. Derive a distinct ovSessionId for the subagent so its messages land
 *      in their own OV session (parent and subagent memories don't mix).
 *   2. Persist a small state record so SubagentStop can replay the subagent
 *      transcript into the correct ovSessionId.
 *
 * Regular in-subagent hooks (PreToolUse, UserPromptSubmit, Stop) do NOT
 * fire — SubagentStart/Stop are the only two events we see for subagents.
 */

import { writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { deriveOvSessionId, isBypassed } from "./lib/ov-session.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const cfg = loadConfig();
const { log, logError } = createLogger("subagent-start");

const STATE_DIR = join(tmpdir(), "openviking-cc-subagent-state");

function approve() {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
}

function stateFile(agentId) {
  const safe = String(agentId).replace(/[^a-zA-Z0-9_-]/g, "_");
  return join(STATE_DIR, `${safe}.json`);
}

async function main() {
  // Paired with subagent-stop.mjs (a write path): when capture is off the
  // stop hook will skip, so there's no point stashing start state either.
  if (!cfg.autoCapture) {
    log("skip", { reason: "autoCapture disabled" });
    approve();
    return;
  }

  let input = {};
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    input = JSON.parse(Buffer.concat(chunks).toString() || "{}");
  } catch { /* best effort */ }

  const sessionId = input.session_id;
  const agentId = input.agent_id;
  const agentType = input.agent_type || "subagent";
  const cwd = input.cwd;

  if (!sessionId || !agentId) {
    log("skip", { reason: "missing session_id or agent_id" });
    approve();
    return;
  }

  if (isBypassed(cfg, { sessionId, cwd })) {
    log("skip", { reason: "bypass_session_pattern" });
    approve();
    return;
  }

  // Isolated ovSessionId: append agent_id so the subagent has its own OV
  // session distinct from the parent (cc-<ccid>__agent-<agentid>).
  const ovSessionId = deriveOvSessionId(sessionId, `agent:${agentId}`);
  const ovAgentId = `${cfg.agentId || "claude-code"}_${agentType}`;

  try {
    await mkdir(STATE_DIR, { recursive: true });
    await writeFile(
      stateFile(agentId),
      JSON.stringify({
        parentSessionId: sessionId,
        agentId,
        agentType,
        ovSessionId,
        ovAgentId,
        startedAt: Date.now(),
      }),
    );
  } catch (err) {
    logError("state_write", err);
  }

  log("start", { agentId, agentType, ovSessionId, ovAgentId });
  approve();
}

main().catch((err) => { logError("uncaught", err); approve(); });
