#!/usr/bin/env node

/**
 * SubagentStop Hook for CodeBuddy.
 *
 * Fires when a subagent finishes. The payload carries the subagent's own
 * transcript as `agent_transcript_path` (with `transcript_path` also pointing
 * at it); verified in P0.
 *
 * In-subagent hooks never fire, so this is the only place a subagent's turns can
 * be captured. We read that transcript with the same decoder auto-capture uses
 * and push the turns to the isolated ovSessionId created in subagent-start.mjs.
 * A commit follows immediately: subagents are short-lived, so threshold tracking
 * would add little, and archiving at the end keeps the subagent's context from
 * lingering as pending.
 *
 * Each subagent becomes its own OV session, `cb-<sessionId>__subagent-<agentId>`.
 */

import { readFile, unlink } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { extractCaptureTurns, parseTranscript } from "./cb-transcript.mjs";
import {
  commitSession,
  deriveOvSessionId,
  enqueuePendingDirectly,
  isRetryableFailure,
  makeFetchJSON,
} from "./lib/ov-session.mjs";
import { maybeDetach, readHookStdin } from "./lib/async-writer.mjs";
import { getEffectivePeerId } from "./lib/workspace-peer.mjs";
import { runHookStage } from "./shared/agent-hook-runtime.mjs";
import { sendSessionMessages } from "./shared/batch-send.mjs";
import { shapeCaptureParts } from "./shared/capture-utils.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const baseCfg = loadConfig();
const { log, logError } = createLogger("subagent-stop");

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

function peerIdFromSubagent(cfg, subagentId, state, sessionId, cwd) {
  if (cfg.peerId) return cfg.peerId;
  if (state?.peerId) return state.peerId;
  const effectivePeer = getEffectivePeerId(cfg, { sessionId, cwd });
  if (effectivePeer.peerId) return effectivePeer.peerId;
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

async function pushTurns(cfg, ovSessionId, turns, { peerId = null, enqueueOnly = false } = {}) {
  const fetchJSON = makeFetchJSON(cfg);
  let ok = 0;
  let queued = 0;
  let failed = 0;
  let enqueueFailed = 0;
  const payloads = [];
  for (const turn of turns) {
    // Structured parts: tool calls/results travel as dedicated `tool` parts so
    // the server can process them separately.
    const parts = shapeCaptureParts(turn.parts, turn.role, cfg).parts;
    if (parts.length === 0) continue;
    const payload = { role: turn.role, parts };
    if (peerId) payload.peer_id = peerId;
    if (enqueueOnly) {
      const res = await enqueuePendingDirectly("addMessage", ovSessionId, payload);
      if (res.ok) queued++;
      else enqueueFailed++;
    } else {
      payloads.push(payload);
    }
  }
  if (!enqueueOnly) {
    const res = await sendSessionMessages(fetchJSON, ovSessionId, payloads, {
      enqueueOnRetryable: true,
    });
    ok = res.sent;
    queued = res.queued;
    failed = res.failed;
    enqueueFailed = res.enqueueFailed;
  }
  // Commit once at the end; subagents are short-lived, so threshold tracking
  // adds little value.
  let committed = false;
  let commitQueued = false;
  let commitTraceId = "";
  if (ok + queued > 0) {
    const commitRes = enqueueOnly
      ? await enqueuePendingDirectly("commitSession", ovSessionId, {})
      : await commitSession(fetchJSON, ovSessionId);
    committed = !enqueueOnly && commitRes.ok;
    commitQueued = enqueueOnly ? Boolean(commitRes.ok) : Boolean(commitRes.pendingQueued);
    commitTraceId = enqueueOnly ? "" : commitRes.traceId || commitRes.result?.trace_id || "";
    if (enqueueOnly && !commitRes.ok) enqueueFailed++;
    else if (!enqueueOnly && commitRes.pendingEnqueueFailed) enqueueFailed++;
  }
  return {
    ok,
    queued,
    failed,
    enqueueFailed,
    committed,
    commitQueued,
    commit_trace_id: commitTraceId || undefined,
  };
}

async function main() {
  // Write-path hook: gated by autoCapture so that disabling capture also
  // suppresses the subagent transcript push + commit.
  if (!baseCfg.autoCapture) {
    log("skip", { reason: "disabled" });
    approve();
    return;
  }

  if (await maybeDetach(baseCfg, { approve })) return;

  await runHookStage({
    loadConfig,
    input: { read: readHookStdin, tolerant: true },
    gates: { enabled: (cfg) => cfg.autoCapture },
    envelope: approve,
    onSkip: (reason) => log("skip", { reason }),
  }, async ({ cfg, input, cwd, sessionId }) => {
    const subagentId = input.agent_id;
    // CodeBuddy names the subagent transcript explicitly; `transcript_path` also
    // carries it, so fall back rather than skip if the explicit key is absent.
    const transcriptPath = input.agent_transcript_path || input.transcript_path;

    if (!sessionId || !subagentId || !transcriptPath) {
      log("skip", { reason: "missing required input fields" });
      return;
    }

    // Prefer the id SubagentStart recorded; re-derive if that state is gone.
    const state = await loadState(subagentId);
    const ovSessionId = state?.ovSessionId || deriveOvSessionId(sessionId, `subagent:${subagentId}`);

    let transcript;
    try {
      transcript = await readFile(transcriptPath, "utf-8");
    } catch (err) {
      logError("transcript_read", err);
      return;
    }

    const messages = parseTranscript(transcript);
    const turns = extractCaptureTurns(messages, cfg);
    log("transcript_parse", { subagentId, ovSessionId, totalTurns: turns.length });

    if (turns.length === 0) {
      await unlink(stateFile(subagentId)).catch(() => {});
      return;
    }

    const peerId = peerIdFromSubagent(cfg, subagentId, state, sessionId, cwd);
    const fetchJSON = makeFetchJSON(cfg);
    const health = await fetchJSON("/health");
    let result;
    if (health.ok) {
      result = await pushTurns(cfg, ovSessionId, turns, { peerId });
    } else if (isRetryableFailure(health)) {
      logError("health_check", "server unreachable; enqueuing subagent capture");
      result = await pushTurns(cfg, ovSessionId, turns, { peerId, enqueueOnly: true });
    } else {
      logError("health_check", `non-retryable status ${health.status || "unknown"}`);
      return;
    }
    log("push_turns", { ovSessionId, ...result });

    if (result.enqueueFailed > 0) {
      logError("pending_enqueue", "some turns failed to enqueue; state file retained");
      return;
    }

    await unlink(stateFile(subagentId)).catch(() => {});
  });
}

main().catch((err) => { logError("uncaught", err); approve(); });
