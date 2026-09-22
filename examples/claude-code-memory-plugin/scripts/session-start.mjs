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
 *      latest_archive_overview, fetched at OPENVIKING_RESUME_CONTEXT_BUDGET
 *      tokens. For "compact" this is OV's canonical long-term record alongside
 *      CC's own compact summary; for "resume" it re-hydrates context lost when
 *      CC restarted.
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
  makeFetchJSON,
} from "./lib/ov-session.mjs";
import { replayPending } from "./lib/pending-queue.mjs";
import {
  buildProfileBlock,
  estimateTokens,
  isRepeatInjection,
  truncateToBytes,
} from "./lib/profile-inject.mjs";
import { statePath, writeJsonState } from "./lib/state.mjs";
import { getEffectivePeerId } from "./lib/workspace-peer.mjs";
import { runHookStage } from "./shared/agent-hook-runtime.mjs";

if (!isPluginEnabled()) {
  process.stdout.write(JSON.stringify({ decision: "approve" }) + "\n");
  process.exit(0);
}

const { log, logError } = createLogger("session-start");
const fetchJSON = makeFetchJSON(loadConfig());

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
function formatArchiveSection(sessionCtx, ovSessionId, maxBytes = 0) {
  if (!sessionCtx || typeof sessionCtx !== "object") return null;
  let overview = (sessionCtx.latest_archive_overview || "").trim();
  if (!overview) return null;
  const truncated = truncateToBytes(overview, maxBytes);
  if (truncated !== overview) {
    overview = `${truncated}\nMore detail: read viking://~/sessions/${ovSessionId}/history/ with the OpenViking MCP read tool.`;
  }

  return [
    "<session-archive>",
    `  <archive-overview>${overview}</archive-overview>`,
    "</session-archive>",
  ].join("\n");
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

runHookStage({
  loadConfig,
  input: { tolerant: true },
  envelope: approve,
  onSkip: (reason) => log("skip", { reason }),
}, async ({ cfg, input, cwd, sessionId }) => {
  const source = input.source || "startup";
  const effectivePeer = getEffectivePeerId(cfg, { sessionId, cwd });
  log("start", { source, sessionId, peerSource: effectivePeer.source });

  const willInjectProfile = !cfg.noAutoInject;
  const willInjectArchive = (source === "resume" || source === "compact") && !!sessionId;

  const health = await fetchJSON("/health");
  if (!health.ok) {
    logError("health_check", "server unreachable");
    return;
  }

  // Pending replay is independent from profile/archive injection. A user may
  // disable injection but still expect failed writes from prior short-lived
  // coding sessions to be recovered when OpenViking is healthy again.
  try {
    const replayResult = await replayPending(fetchJSON, log);
    if (replayResult.replayed > 0 || replayResult.failed > 0 || replayResult.deferred > 0) {
      log("pending-replay", replayResult);
    }
  } catch (err) {
    logError("pending-replay", err);
  }

  if (!willInjectProfile && !willInjectArchive) {
    log("skip", { reason: "no_injection_planned", source, noAutoInject: cfg.noAutoInject });
    return;
  }

  // 1. Archive injection — resume/compact only, requires session_id. Under a
  // byte cap it takes up to half, and the profile gets the rest.
  const maxBytes = cfg.sessionStartMaxBytes || 0;
  let archiveSection = null;
  let ovSessionId = null;
  if ((source === "resume" || source === "compact") && sessionId) {
    ovSessionId = deriveOvSessionId(sessionId);
    const sessionCtx = await getSessionContext(fetchJSON, ovSessionId, cfg.resumeContextBudget);
    archiveSection = formatArchiveSection(sessionCtx, ovSessionId, Math.floor(maxBytes / 2));
  }

  // 2. Profile injection — every source unless explicitly disabled. A resumed
  // session already holds the earlier block, so an unchanged one is skipped.
  let profile = null;
  if (!cfg.noAutoInject) {
    try {
      const profileMaxBytes = maxBytes > 0
        ? Math.max(1, maxBytes - Buffer.byteLength(archiveSection || "", "utf8") - 100)
        : 0;
      profile = await buildProfileBlock(fetchJSON, cfg.profileTokenBudget, effectivePeer.peerId, {
        ...cfg,
        sessionStartMaxBytes: profileMaxBytes,
      });
      if (profile?.block && sessionId) {
        const repeat = isRepeatInjection(statePath("profile-injections.json"), sessionId, profile.block);
        if (repeat && source === "resume") {
          log("profile_inject_skipped", { reason: "unchanged since last injection", source });
          profile = null;
        }
      }
    } catch (err) {
      logError("profile_inject", err);
    }
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
    return;
  }

  const composed = `<openviking-context source="${source}">\n${sections.join("\n")}\n</openviking-context>`;
  writeLastInject(composed);

  if (cfg.debug) {
    process.stderr.write(
      `[ov] session-start injected ~${composed.length} chars / ~${estimateTokens(composed)} tokens` +
      (profile ? ` (profile=${profile.profileChars} chars, prefs=${profile.prefCount}${profile.droppedPref ? `(+${profile.droppedPref} dropped)` : ""}, entities=${profile.entCount}${profile.droppedEnt ? `(+${profile.droppedEnt} dropped)` : ""}, skills=${profile.skillCount}${profile.droppedSkill ? `(+${profile.droppedSkill} dropped)` : ""})` : "") +
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
  return composed;
}).catch((err) => { logError("uncaught", err); approve(); });
