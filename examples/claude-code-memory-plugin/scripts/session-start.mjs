#!/usr/bin/env node

/**
 * SessionStart Hook for Claude Code.
 *
 * When source in {"resume","compact"}, fetches the persistent OV session's
 * latest_archive_overview and injects it as additionalContext wrapped in
 * <openviking-context>. For "compact", this supplies OV's canonical
 * long-term record alongside CC's own compact summary; for "resume",
 * it re-hydrates the context that was lost when CC restarted.
 */

import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import {
  deriveOvSessionId,
  getSessionContext,
  isBypassed,
  makeFetchJSON,
} from "./lib/ov-session.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const cfg = loadConfig();
const { log, logError } = createLogger("session-start");
const fetchJSON = makeFetchJSON(cfg);

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function approve(additionalContext) {
  const out = { decision: "approve" };
  if (additionalContext) {
    out.hookSpecificOutput = {
      hookEventName: "SessionStart",
      additionalContext,
    };
  }
  output(out);
}

/**
 * Build <openviking-context> block from session context.
 * Pulls latest_archive_overview (pre-archive abstracts list if populated).
 */
function formatArchiveContext(sessionCtx, source) {
  if (!sessionCtx || typeof sessionCtx !== "object") return null;
  const overview = (sessionCtx.latest_archive_overview || "").trim();
  if (!overview) return null;

  const abstracts = Array.isArray(sessionCtx.pre_archive_abstracts)
    ? sessionCtx.pre_archive_abstracts.filter(a => typeof a === "string" && a.trim())
    : [];

  const lines = [
    `<openviking-context source="${source}">`,
    `  <archive-overview>${overview}</archive-overview>`,
  ];
  for (const abs of abstracts.slice(0, 5)) {
    lines.push(`  <archive-abstract>${abs.trim()}</archive-abstract>`);
  }
  lines.push("</openviking-context>");
  return lines.join("\n");
}

async function main() {
  let input = {};
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    input = JSON.parse(Buffer.concat(chunks).toString() || "{}");
  } catch { /* best effort */ }

  const source = input.source || "startup";
  const sessionId = input.session_id;
  const cwd = input.cwd;
  log("start", { source, sessionId });

  // Only inject archive context on resume/compact. startup/clear get nothing.
  if (source !== "resume" && source !== "compact") {
    approve();
    return;
  }
  if (!sessionId) {
    log("skip", { reason: "no session_id" });
    approve();
    return;
  }

  if (isBypassed(cfg, { sessionId, cwd })) {
    log("skip", { reason: "bypass_session_pattern" });
    approve();
    return;
  }

  const health = await fetchJSON("/health");
  if (!health.ok) {
    logError("health_check", "server unreachable");
    approve();
    return;
  }

  const ovSessionId = deriveOvSessionId(sessionId);
  const sessionCtx = await getSessionContext(fetchJSON, ovSessionId, cfg.resumeContextBudget);
  const block = formatArchiveContext(sessionCtx, source);
  if (!block) {
    log("no_archive", { ovSessionId, source });
    approve();
    return;
  }

  log("inject", { ovSessionId, source, blockLength: block.length });
  approve(block);
}

main().catch((err) => { logError("uncaught", err); approve(); });
