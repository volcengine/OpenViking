#!/usr/bin/env node

/**
 * UserPromptSubmit hook (assemble): full context assembly.
 * 1. Fetches session context (archive summaries) from OV
 * 2. Searches memories (user + agent) with query-aware ranking
 * 3. Injects assembled context as systemMessage
 *
 * Runs session context fetch + memory search in PARALLEL to fit 8s timeout.
 */

import { loadConfig } from "./config.mjs";
import { createClient } from "./http-client.mjs";
import { loadState, deriveOvSessionId } from "./state.mjs";
import { createLogger } from "./debug-log.mjs";
import { postProcess, pickMemories } from "./ranking.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("assemble");

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function approve(msg) {
  const out = { decision: "approve" };
  if (msg) out.hookSpecificOutput = { hookEventName: "UserPromptSubmit", additionalContext: msg };
  output(out);
}

// Deadline: abort everything if we're close to the 8s hook timeout
const DEADLINE_MS = 6500;
const startTime = Date.now();
function timeLeft() { return DEADLINE_MS - (Date.now() - startTime); }

async function withDeadline(promise) {
  const remaining = timeLeft();
  if (remaining <= 0) return null;
  return Promise.race([
    promise,
    new Promise(resolve => setTimeout(() => resolve(null), remaining)),
  ]);
}

async function searchBothScopes(client, query, limit) {
  const [userMems, agentMems, agentSkills] = await Promise.allSettled([
    client.find(query, "viking://user/memories", limit),
    client.find(query, "viking://agent/memories", limit),
    client.find(query, "viking://agent/skills", limit),
  ]);

  const user = userMems.status === "fulfilled" ? userMems.value : [];
  const agent = agentMems.status === "fulfilled" ? agentMems.value : [];
  const skills = agentSkills.status === "fulfilled" ? agentSkills.value : [];

  const all = [...user, ...agent, ...skills];
  const uriSet = new Set();
  return all.filter(m => {
    if (uriSet.has(m.uri)) return false;
    uriSet.add(m.uri);
    return true;
  });
}

async function main() {
  if (!cfg.autoRecall) {
    log("skip", { reason: "autoRecall disabled" });
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

  const userPrompt = (input.prompt || "").trim();
  log("start", { queryLength: userPrompt.length });

  if (!userPrompt || userPrompt.length < cfg.minQueryLength) {
    log("skip", { reason: "query too short" });
    approve();
    return;
  }

  // Health check
  const client = createClient(cfg);
  const healthy = await withDeadline(client.healthCheck());
  if (!healthy) {
    logError("health_check", "server unreachable");
    approve();
    return;
  }

  // Load state for session context
  // Try to get session_id from env or use a default
  const ccSessionId = process.env.CLAUDE_SESSION_ID || "unknown";
  const state = { ovSessionId: deriveOvSessionId(ccSessionId) };

  // Run in parallel: session context + memory search
  const candidateLimit = Math.max(cfg.recallLimit * 4, 20);
  const [sessionCtx, allMemories] = await Promise.all([
    withDeadline(client.getSessionContext(state.ovSessionId, cfg.contextTokenBudget)),
    withDeadline(searchBothScopes(client, userPrompt, candidateLimit)),
  ]);

  const sections = [];

  // --- Session Context (archive summaries) ---
  if (sessionCtx) {
    const parts = [];

    if (sessionCtx.latest_archive_overview) {
      parts.push(`[Session History Summary]\n${sessionCtx.latest_archive_overview}`);
    }

    if (sessionCtx.pre_archive_abstracts && sessionCtx.pre_archive_abstracts.length > 0) {
      const archiveLines = sessionCtx.pre_archive_abstracts
        .map(a => `${a.archive_id}: ${a.abstract || "..."}`)
        .join("\n");
      parts.push(`[Archive Index]\n${archiveLines}`);
    }

    if (parts.length > 0) {
      parts.push(
        "\n## Session Context Guide\n" +
        "The above shows compressed history from earlier in this conversation. " +
        "If you need original details from a specific archive, use the `ov_archive_expand` tool with the archive_id."
      );

      sections.push(
        "<openviking-context>\n" + parts.join("\n\n") + "\n</openviking-context>"
      );

      log("session_context", {
        hasOverview: !!sessionCtx.latest_archive_overview,
        archiveCount: sessionCtx.pre_archive_abstracts?.length || 0,
        estimatedTokens: sessionCtx.estimatedTokens,
      });
    }
  }

  // --- Memory Recall ---
  if (allMemories && allMemories.length > 0) {
    const processed = postProcess(allMemories, candidateLimit, cfg.scoreThreshold);
    const memories = pickMemories(processed, cfg.recallLimit, userPrompt);

    log("recall", {
      rawCount: allMemories.length,
      processedCount: processed.length,
      pickedCount: memories.length,
    });

    if (memories.length > 0) {
      // Read full content for leaf memories (with deadline)
      const lines = await Promise.all(
        memories.map(async (item) => {
          if (item.level === 2 && timeLeft() > 500) {
            const content = await withDeadline(client.read(item.uri));
            if (content) return `- [${item.category || "memory"}] ${content}`;
          }
          return `- [${item.category || "memory"}] ${(item.abstract || item.overview || item.uri).trim()}`;
        })
      );

      sections.push(
        "<relevant-memories>\n" +
        "The following long-term memories from OpenViking may be relevant to this conversation:\n" +
        lines.join("\n") + "\n" +
        "</relevant-memories>"
      );
    }
  }

  if (sections.length === 0) {
    log("skip", { reason: "no context to inject" });
    approve();
    return;
  }

  approve(sections.join("\n\n"));
}

main().catch((err) => { logError("uncaught", err); approve(); });
