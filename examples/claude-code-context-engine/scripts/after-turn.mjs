#!/usr/bin/env node

/**
 * Stop hook (afterTurn): read transcript, capture new turns to persistent OV session,
 * evaluate compact thresholds, trigger session.commit() when needed.
 *
 * Key difference from old auto-capture.mjs:
 * - Maintains PERSISTENT session (not temp sessions)
 * - Messages accumulate; commit triggers server-side archiving + memory extraction
 * - Evaluates multiple compact thresholds (tokens, turns, interval, tokens-added)
 */

import { readFile } from "node:fs/promises";
import { loadConfig } from "./config.mjs";
import { createClient } from "./http-client.mjs";
import { loadState, saveState, deriveOvSessionId } from "./state.mjs";
import { createLogger } from "./debug-log.mjs";
import { parseTranscript, extractAllTurns, sanitize, estimateTokens, groupTurns } from "./text-utils.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("after-turn");

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function approve(msg) {
  const out = { decision: "approve" };
  if (msg) out.systemMessage = msg;
  output(out);
}

async function main() {
  if (!cfg.autoCapture) {
    log("skip", { reason: "autoCapture disabled" });
    approve();
    return;
  }

  // Read stdin
  let input;
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    input = JSON.parse(Buffer.concat(chunks).toString());
  } catch {
    log("skip", { reason: "invalid stdin" });
    approve();
    return;
  }

  const transcriptPath = input.transcript_path;
  const ccSessionId = input.session_id || "unknown";
  log("start", { ccSessionId, transcriptPath });

  // Health check
  const client = createClient(cfg);
  const healthy = await client.healthCheck();
  if (!healthy) {
    logError("health_check", "server unreachable");
    approve();
    return;
  }

  if (!transcriptPath) {
    log("skip", { reason: "no transcript_path" });
    approve();
    return;
  }

  // Read transcript
  let transcriptContent;
  try {
    transcriptContent = await readFile(transcriptPath, "utf-8");
  } catch (err) {
    logError("transcript_read", err);
    approve();
    return;
  }

  if (!transcriptContent.trim()) {
    log("skip", { reason: "empty transcript" });
    approve();
    return;
  }

  // Parse and extract all turns
  const messages = parseTranscript(transcriptContent);
  const allTurns = extractAllTurns(messages);
  if (allTurns.length === 0) {
    log("skip", { reason: "no turns" });
    approve();
    return;
  }

  // Load state
  const state = await loadState(ccSessionId);
  if (!state.ovSessionId) {
    state.ovSessionId = deriveOvSessionId(ccSessionId);
  }
  const ovSessionId = state.ovSessionId;

  // Incremental: only new turns
  const newTurns = allTurns.slice(state.capturedTurnCount);
  log("transcript_parse", {
    totalTurns: allTurns.length,
    previouslyCaptured: state.capturedTurnCount,
    newTurns: newTurns.length,
  });

  if (newTurns.length === 0) {
    approve();
    return;
  }

  // Ensure OV session exists
  const session = await client.getSession(ovSessionId);
  if (!session) {
    const created = await client.createSession(ovSessionId);
    if (!created) {
      logError("session_create", "failed to create OV session");
      approve();
      return;
    }
  }

  // Send new turns to OV session
  // Group adjacent same-role messages
  const groups = groupTurns(newTurns);
  let tokensAdded = 0;

  for (const group of groups) {
    const text = sanitize(group.texts.join("\n"));
    if (!text) continue;

    const result = await client.addSessionMessage(ovSessionId, group.role, text);
    if (result) {
      tokensAdded += estimateTokens(text);
    }
  }

  // Update state
  state.capturedTurnCount = allTurns.length;
  state.totalTokensAdded += tokensAdded;
  state.turnsSinceCommit += newTurns.length;

  log("capture_done", {
    groupsSent: groups.length,
    tokensAdded,
    totalTokensAdded: state.totalTokensAdded,
    turnsSinceCommit: state.turnsSinceCommit,
  });

  // Evaluate compact thresholds
  const now = Date.now();
  const intervalSinceCommit = now - (state.lastCommitAt || state.createdAt || now);
  let commitReason = null;

  if (state.totalTokensAdded >= cfg.commitTokensAddedThreshold) {
    commitReason = `tokensAdded(${state.totalTokensAdded} >= ${cfg.commitTokensAddedThreshold})`;
  } else if (state.turnsSinceCommit >= cfg.commitTurnThreshold) {
    commitReason = `turns(${state.turnsSinceCommit} >= ${cfg.commitTurnThreshold})`;
  } else if (intervalSinceCommit >= cfg.commitIntervalMs && state.turnsSinceCommit >= 1) {
    commitReason = `interval(${Math.floor(intervalSinceCommit / 60000)}min >= ${Math.floor(cfg.commitIntervalMs / 60000)}min)`;
  }

  // Also check server-side pending tokens
  if (!commitReason) {
    const sessionInfo = await client.getSession(ovSessionId);
    const pendingTokens = sessionInfo?.pending_tokens || sessionInfo?.message_count * 250 || 0;
    if (pendingTokens >= cfg.commitTokenThreshold) {
      commitReason = `pendingTokens(${pendingTokens} >= ${cfg.commitTokenThreshold})`;
    }
  }

  if (commitReason) {
    log("compact_trigger", { reason: commitReason });

    const commitResult = await client.commitSession(ovSessionId);
    if (commitResult) {
      log("compact_done", {
        taskId: commitResult.task_id,
        archived: commitResult.archived,
        archiveUri: commitResult.archive_uri,
      });

      state.lastCommitAt = now;
      state.totalTokensAdded = 0;
      state.turnsSinceCommit = 0;
      state.commitCount++;
    } else {
      logError("compact_failed", "commitSession returned null");
    }
  }

  await saveState(ccSessionId, state);
  approve();
}

main().catch((err) => { logError("uncaught", err); approve(); });
