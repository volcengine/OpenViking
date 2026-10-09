#!/usr/bin/env node

/**
 * SubagentStart Hook for CodeBuddy.
 *
 * Fires when the parent session spawns a subagent through the Agent tool. The
 * payload carries `session_id`, `agent_id`, `agent_type` and — unlike the
 * parent — a `transcript_path` that already points at the subagent's own file
 * (`<session>/subagents/agent-<uuid>.jsonl`); verified in P0.
 *
 * We do two things:
 *   1. Derive a distinct ovSessionId for the subagent so its turns land in
 *      their own OV session instead of mixing with the parent's.
 *   2. Persist a small state record so SubagentStop can reuse that id rather
 *      than re-deriving it from a payload that may have changed.
 *
 * In-subagent hooks (PreToolUse, Stop, …) were never observed firing during the
 * P0 probe, so SubagentStart/SubagentStop are the only two events a subagent
 * gives us — see docs/HOST-CONTRACT.md §4.
 *
 * State lives under ${CODEBUDDY_PLUGIN_DATA} (kept across plugin updates), the
 * same place capture state lives. Read the directory from the env var: with
 * `--plugin-dir` the plugin id gains an `-inline` suffix.
 */

import { writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { deriveOvSessionId } from "./lib/ov-session.mjs";
import { getEffectivePeerId } from "./lib/workspace-peer.mjs";
import { runHookStage } from "./shared/agent-hook-runtime.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const { log, logError } = createLogger("subagent-start");

const STATE_DIR = process.env.CODEBUDDY_PLUGIN_DATA
  ? join(process.env.CODEBUDDY_PLUGIN_DATA, "subagent-state")
  : join(tmpdir(), "openviking-cb-subagent-state");

function approve() {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
}

function stateFile(subagentId) {
  const safe = String(subagentId).replace(/[^a-zA-Z0-9_-]/g, "_");
  return join(STATE_DIR, `${safe}.json`);
}

runHookStage({
  loadConfig,
  input: { tolerant: true },
  // Paired with subagent-stop.mjs (a write path): when capture is off the stop
  // hook will skip, so there is no point stashing start state either.
  gates: { enabled: (cfg) => cfg.autoCapture },
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

  // Isolated ovSessionId: the subagent gets its own OV session, distinct from
  // the parent's cb-<sessionId>.
  const ovSessionId = deriveOvSessionId(sessionId, `subagent:${subagentId}`);

  try {
    await mkdir(STATE_DIR, { recursive: true });
    await writeFile(
      stateFile(subagentId),
      JSON.stringify({
        parentSessionId: sessionId,
        subagentId,
        agentType,
        ovSessionId,
        peerId: effectivePeer.peerId,
        peerSource: effectivePeer.source,
        startedAt: Date.now(),
      }),
    );
  } catch (err) {
    logError("state_write", err);
  }

  log("start", { subagentId, agentType, ovSessionId, peerSource: effectivePeer.source });
}).catch((err) => { logError("uncaught", err); approve(); });
