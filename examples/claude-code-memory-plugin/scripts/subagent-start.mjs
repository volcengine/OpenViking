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

import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { deriveOvSessionId } from "./lib/ov-session.mjs";
import { readCursor, writeCursor } from "./lib/state.mjs";
import { getEffectivePeerId } from "./lib/workspace-peer.mjs";
import { runHookStage } from "./shared/agent-hook-runtime.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const { log, logError } = createLogger("subagent-start");

function approve() {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
}

runHookStage({
  loadConfig,
  input: { tolerant: true },
  // Paired with subagent-stop.mjs (a write path): when capture is off, subagent
  // sessions are skipped, capture is in keyword mode or assistant turns are
  // off, the stop hook will skip, so there's no point stashing start state.
  gates: {
    enabled: (cfg) => cfg.autoCapture
      && !cfg.skipSubagentSessions
      && cfg.captureMode !== "keyword"
      && cfg.captureAssistantTurns,
  },
  envelope: approve,
  onSkip: (reason) => log("skip", { reason }),
}, async ({ cfg, input, cwd, sessionId }) => {
  const subagentId = input.agent_id;
  const agentType = input.agent_type || "subagent";

  if (!sessionId || !subagentId) {
    log("skip", { reason: "missing session_id or agent_id" });
    return;
  }

  const effectivePeer = getEffectivePeerId(cfg, { sessionId, cwd });

  // Isolated ovSessionId: append Claude's subagent id so the subagent has its
  // own OV session distinct from the parent.
  const ovSessionId = deriveOvSessionId(sessionId, `subagent:${subagentId}`);

  // A resumed subagent keeps its agent_id; keep the capture cursor so its next
  // stop does not resend turns an earlier stop already pushed.
  const capturedTurnCount = (await readCursor("subagent", subagentId))?.capturedTurnCount;

  try {
    await writeCursor("subagent", subagentId, {
      parentSessionId: sessionId,
      subagentId,
      agentType,
      ovSessionId,
      peerId: effectivePeer.peerId,
      peerSource: effectivePeer.source,
      startedAt: Date.now(),
      capturedTurnCount,
    });
  } catch (err) {
    logError("state_write", err);
  }

  log("start", { subagentId, agentType, ovSessionId, peerSource: effectivePeer.source });
}).catch((err) => { logError("uncaught", err); approve(); });
