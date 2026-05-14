#!/usr/bin/env node

/**
 * SessionStart Hook for Claude Code.
 *
 * Two independently-gated injections, composed into a single
 * <openviking-context source="..."> envelope returned via additionalContext:
 *
 *   1. Profile injection (every source: startup/clear/resume/compact unless
 *      OPENVIKING_NO_AUTO_INJECT=1): full profile.md + description-annotated
 *      ls of preferences/ and entities/. Total capped at
 *      OPENVIKING_PROFILE_TOKEN_BUDGET (default 10000 tokens, CJK-aware).
 *
 *   2. Archive injection (resume/compact only): OV's persistent session's
 *      latest_archive_overview + pre-archive abstracts, fetched at
 *      OPENVIKING_RESUME_CONTEXT_BUDGET tokens. For "compact" this is OV's
 *      canonical long-term record alongside CC's own compact summary; for
 *      "resume" it re-hydrates context lost when CC restarted.
 *
 * The composed payload is mirrored to ~/.openviking/last_inject.md for audit.
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

import { isPluginEnabled, loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import {
  deriveOvSessionId,
  getSessionContext,
  isBypassed,
  makeFetchJSON,
} from "./lib/ov-session.mjs";
import { buildProfileBlock, estimateTokens } from "./lib/profile-inject.mjs";
import { writeJsonState } from "./lib/state.mjs";

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
 * Build the inner <session-archive> block from session context.
 * Returns null when there is no archive content yet.
 */
function formatArchiveSection(sessionCtx) {
  if (!sessionCtx || typeof sessionCtx !== "object") return null;
  const overview = (sessionCtx.latest_archive_overview || "").trim();
  if (!overview) return null;

  const abstracts = Array.isArray(sessionCtx.pre_archive_abstracts)
    ? sessionCtx.pre_archive_abstracts.filter((a) => typeof a === "string" && a.trim())
    : [];

  const lines = [
    "<session-archive>",
    `  <archive-overview>${overview}</archive-overview>`,
  ];
  for (const abs of abstracts.slice(0, 5)) {
    lines.push(`  <archive-abstract>${abs.trim()}</archive-abstract>`);
  }
  lines.push("</session-archive>");
  return lines.join("\n");
}

function writeLastInject(content) {
  try {
    const path = join(homedir(), ".openviking", "last_inject.md");
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, content, "utf-8");
  } catch {
    /* best effort — last_inject.md is for audit only */
  }
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

  if (isBypassed(cfg, { sessionId, cwd })) {
    log("skip", { reason: "bypass_session_pattern" });
    approve();
    return;
  }

  // Short-circuit before the network probe when neither injection path will
  // run for this source/config combination. Saves a /health call and avoids
  // misleading "server unreachable" log noise when injection is disabled.
  const willInjectProfile = !cfg.noAutoInject;
  const willInjectArchive = (source === "resume" || source === "compact") && !!sessionId;
  if (!willInjectProfile && !willInjectArchive) {
    log("skip", { reason: "no_injection_planned", source, noAutoInject: cfg.noAutoInject });
    approve();
    return;
  }

  const health = await fetchJSON("/health");
  if (!health.ok) {
    logError("health_check", "server unreachable");
    approve();
    return;
  }

  // 1. Profile injection — every source unless explicitly disabled.
  let profile = null;
  if (!cfg.noAutoInject) {
    try {
      profile = await buildProfileBlock(fetchJSON, cfg.profileTokenBudget);
    } catch (err) {
      logError("profile_inject", err);
    }
  }

  // 2. Archive injection — resume/compact only, requires session_id.
  let archiveSection = null;
  let ovSessionId = null;
  if ((source === "resume" || source === "compact") && sessionId) {
    ovSessionId = deriveOvSessionId(sessionId);
    const sessionCtx = await getSessionContext(fetchJSON, ovSessionId, cfg.resumeContextBudget);
    archiveSection = formatArchiveSection(sessionCtx);
  }

  // One-shot signal for the statusline (preserved from prior behavior:
  // statusline expects this on every resume/compact, regardless of whether
  // OV had archive content to inject).
  if (source === "resume" || source === "compact") {
    writeJsonState("last-session-event.json", {
      source,
      cc_session_id: sessionId,
      ov_session_id: ovSessionId,
      had_context: Boolean(archiveSection),
    });
  }

  // Compose. If both halves are empty, return without injecting.
  const sections = [];
  if (profile?.block) sections.push(profile.block);
  if (archiveSection) sections.push(archiveSection);

  if (sections.length === 0) {
    log("no_inject", { source, profile: !!profile, archive: !!archiveSection });
    approve();
    return;
  }

  const composed = `<openviking-context source="${source}">\n${sections.join("\n")}\n</openviking-context>`;
  writeLastInject(composed);

  if (cfg.debug) {
    process.stderr.write(
      `[ov] session-start injected ~${composed.length} chars / ~${estimateTokens(composed)} tokens` +
      (profile ? ` (profile=${profile.profileChars} chars, prefs=${profile.prefCount}${profile.droppedPref ? `(+${profile.droppedPref} dropped)` : ""}, entities=${profile.entCount}${profile.droppedEnt ? `(+${profile.droppedEnt} dropped)` : ""})` : "") +
      (archiveSection ? " +archive" : "") +
      "\n",
    );
  }

  log("inject", {
    source,
    chars: composed.length,
    tokens: estimateTokens(composed),
    profile: profile && {
      tokens: profile.tokens,
      profileChars: profile.profileChars,
      prefCount: profile.prefCount,
      entCount: profile.entCount,
      droppedPref: profile.droppedPref,
      droppedEnt: profile.droppedEnt,
    },
    archive: Boolean(archiveSection),
  });
  approve(composed);
}

main().catch((err) => { logError("uncaught", err); approve(); });
