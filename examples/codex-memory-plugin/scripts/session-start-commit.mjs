#!/usr/bin/env node

/**
 * SessionStart hook for Codex.
 *
 * Triggers (matcher = "clear|startup|resume" in hooks.json):
 *   - source=startup → fresh codex CLI / `/new` / zouk daemon spawn-without-sessionId
 *   - source=clear   → `/clear` (orphans the current process's previous session)
 *   - source=resume  → `/resume` or short reconnect (no commit/sweep;
 *     may inject latest archive summary if the live OV session was already committed)
 *
 * Every source injects the shared OpenViking profile block unless
 * OPENVIKING_NO_AUTO_INJECT=1. The block contains profile.md plus
 * abstract-annotated indexes of preferences/ and entities/, capped by
 * OPENVIKING_PROFILE_TOKEN_BUDGET with the shared CJK-aware estimator.
 *
 * Behavior (see DESIGN.md — "Fallback sweep"):
 *   Committing a finished thread is SessionEnd's job. On `startup` or `clear`
 *   this hook only sweeps what SessionEnd could not reach: a state file with a
 *   live OV session is committed when it carries an `.ended` marker (the
 *   SessionEnd worker was killed or the server was down) or when it has been
 *   idle for more than IDLE_TTL_MS (default 30 min) — signals, crashes, Codex
 *   older than 0.145, and app-server threads whose SessionEnd is deferred.
 *
 *   Each candidate is committed under its session lock with no waiting: a lock
 *   we cannot take means a SessionEnd or Stop worker already owns the session.
 *   Under the lock the sweep first appends whatever the state's recorded
 *   transcript still holds past the cursor, so a session whose workers never
 *   ran is not archived without its tail turns; an incomplete append keeps the
 *   session live for the next sweep instead of committing.
 *
 *   The same pass retires cursor-only states (no live OV session): a cursor
 *   that was never used is dropped after IDLE_TTL_MS, and a real cursor is
 *   kept for resume until COMMITTED_TTL_MS (default 30 days). Without this the
 *   state directory grows one file per codex session forever and listStates()
 *   reads all of them on every SessionStart.
 *
 * Commit failure handling:
 *   On any /commit failure (OV unreachable, non-2xx, timeout) we keep the state
 *   file with ovSessionId still set so the next sweep retries. A transient OV
 *   outage shouldn't lose memory.
 *
 * Output may contain hookSpecificOutput.additionalContext for profile/archive
 * injection and systemMessage for commit status at the same time.
 */

import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { catchUpTurns, makeFetchJSON } from "./ov-session.mjs";
import { detectRecallCompressorProfile } from "./recall-compressor-profile.mjs";
import {
  clearEnded,
  clearState,
  deriveOvSessionId,
  listStates,
  loadState,
  readEndedAt,
  saveState,
  withSessionLock,
} from "./session-state.mjs";
import { buildProfileBlock } from "./shared/profile-inject.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";

let cfg = loadConfig();
const { log, logError } = createLogger("session-start");
let activePeerId = cfg.peerId || "";

const IDLE_TTL_MS = (() => {
  const v = Number(process.env.OPENVIKING_CODEX_IDLE_TTL_MS);
  return Number.isFinite(v) && v > 0 ? Math.floor(v) : 1_800_000;
})();

const HOOK_STARTED_AT = Date.now();

// The sweep catches up unsent turns before committing, so it needs the same
// HTTP helper the capture hooks use.
const { fetchJSONRes } = makeFetchJSON(cfg, { getActorPeerId: () => activePeerId });

const COMMITTED_TTL_MS = (() => {
  const v = Number(process.env.OPENVIKING_CODEX_COMMITTED_TTL_MS);
  return Number.isFinite(v) && v > 0 ? Math.floor(v) : 2_592_000_000;
})();

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function noop(message) {
  output(message ? { systemMessage: message } : {});
}

function emitSessionStartOutput({ contexts = [], systemMessage = "" } = {}) {
  const additionalContext = contexts.filter(Boolean).join("\n\n");
  const response = {};
  if (additionalContext) {
    response.hookSpecificOutput = {
      hookEventName: "SessionStart",
      additionalContext,
    };
  }
  if (systemMessage) response.systemMessage = systemMessage;
  output(response);
}

function responseTraceId(body) {
  return body?.result?.trace_id || body?.error?.trace_id || body?.trace_id || undefined;
}

async function requestJSON(path, init = {}, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), cfg.captureTimeoutMs);
  try {
    const headers = { "Content-Type": "application/json" };
    if (cfg.apiKey) {
      headers["Authorization"] = `Bearer ${cfg.apiKey}`;
      headers["X-API-Key"] = cfg.apiKey;
    }
    if (cfg.sendIdentityHeaders && cfg.account) headers["X-OpenViking-Account"] = cfg.account;
    if (cfg.sendIdentityHeaders && cfg.user) headers["X-OpenViking-User"] = cfg.user;
    const actorPeerId = options.actorPeerId ?? activePeerId;
    if (actorPeerId) headers["X-OpenViking-Actor-Peer"] = actorPeerId;
    if (cfg.userAgent) headers["User-Agent"] = cfg.userAgent;
    const res = await fetch(`${cfg.baseUrl}${path}`, { ...init, headers, signal: controller.signal });
    const body = await res.json().catch(() => null);
    if (!body) return { ok: false, status: res.status };
    const traceId = responseTraceId(body);
    if (!res.ok || body.status === "error") {
      return { ok: false, status: res.status, error: body.error || body, traceId };
    }
    return { ok: true, status: res.status, result: body.result ?? body, traceId };
  } catch (error) {
    return { ok: false, status: 0, error: { message: error?.message || String(error) } };
  } finally {
    clearTimeout(timer);
  }
}

async function fetchJSON(path, init = {}, options = {}) {
  const response = await requestJSON(path, init, options);
  return response.ok ? response.result : null;
}

async function commitOvSession(ovSessionId) {
  if (!ovSessionId) return null;
  return requestJSON(
    `/api/v1/sessions/${encodeURIComponent(ovSessionId)}/commit`,
    { method: "POST", body: JSON.stringify({}) },
  );
}

function truncateText(text, maxChars) {
  const value = String(text || "").trim();
  if (value.length <= maxChars) return value;
  return `${value.slice(0, Math.max(0, maxChars - 20)).trimEnd()}\n[truncated]`;
}

function formatResumeArchiveContext(ovSessionId, context) {
  const overview = String(context?.latest_archive_overview || "").trim();
  if (!overview) return "";
  const archiveUri = `viking://~/sessions/${ovSessionId}/history/`;
  const body = truncateText(overview, cfg.resumeArchiveMaxChars);
  return [
    "OpenViking session archive digest:",
    `Latest committed archive for resumed Codex session ${ovSessionId}:`,
    body,
    "",
    `More detail: use the OpenViking MCP read/search tools with ${archiveUri} if you need exact prior commands, files, tool outputs, or messages.`,
  ].join("\n");
}

function wrapResumeContext(additionalContext) {
  const body = String(additionalContext || "")
    .replace(/<\/?openviking-context\b[^>]*>/gi, "openviking context marker")
    .trim();
  if (!body) return "";
  return [
    '<openviking-context source="session-resume" format="archive-digest">',
    body,
    "</openviking-context>",
  ].join("\n");
}

function wrapProfileContext(profileBlock) {
  if (!profileBlock) return "";
  return [
    '<openviking-context source="session-start">',
    profileBlock,
    "</openviking-context>",
  ].join("\n");
}

async function buildSessionProfileContext() {
  if (cfg.noAutoInject) {
    log("skip", { stage: "profile_inject", reason: "disabled" });
    return "";
  }
  try {
    const profile = await buildProfileBlock(
      requestJSON,
      cfg.profileTokenBudget,
      activePeerId,
    );
    if (!profile?.block) {
      log("skip", { stage: "profile_inject", reason: "no profile content" });
      return "";
    }
    log("profile_inject", {
      chars: profile.chars,
      tokens: profile.tokens,
      profileChars: profile.profileChars,
      prefCount: profile.prefCount,
      entCount: profile.entCount,
      droppedPref: profile.droppedPref,
      droppedEnt: profile.droppedEnt,
    });
    return wrapProfileContext(profile.block);
  } catch (error) {
    logError("profile_inject", error);
    return "";
  }
}

async function buildResumeArchiveContext(newSessionId) {
  if (!cfg.resumeArchiveInject) {
    log("skip", { stage: "resume_archive", reason: "disabled" });
    return "";
  }

  const state = await loadState(newSessionId);
  if (state.ovSessionId) {
    log("skip", {
      stage: "resume_archive",
      reason: "live OV session still open",
      ovSessionId: state.ovSessionId,
    });
    return "";
  }

  const ovSessionId = deriveOvSessionId(newSessionId);
  const context = await fetchJSON(
    `/api/v1/sessions/${encodeURIComponent(ovSessionId)}/context?token_budget=${cfg.resumeArchiveTokenBudget}`,
  );
  const additionalContext = formatResumeArchiveContext(ovSessionId, context);
  if (!additionalContext) {
    log("skip", { stage: "resume_archive", reason: "no archive overview", ovSessionId });
    return "";
  }

  log("resume_archive_inject", {
    ovSessionId,
    chars: additionalContext.length,
    tokenBudget: cfg.resumeArchiveTokenBudget,
  });
  return wrapResumeContext(additionalContext);
}

/**
 * Commit a live OV session and release it, keeping the transcript cursor so a
 * later resume appends instead of replaying (DESIGN.md "Commit-then-resume").
 * On commit failure, keep the live session id so the next sweep retries.
 *
 * Returns { committed: bool, ovSessionId: string|null, traceId: string }.
 */
async function commitAndRelease(state, reason, endToken) {
  const ovSessionId = state.ovSessionId;
  const commit = await commitOvSession(ovSessionId);
  if (!commit?.ok) {
    log("commit", {
      reason,
      codexSessionId: state.codexSessionId,
      ovSessionId,
      ok: false,
      status: commit?.status,
      trace_id: commit?.traceId,
      error: commit?.error?.message || commit?.error?.code,
    });
    return { committed: false, ovSessionId: null, traceId: commit?.traceId || "" };
  }
  const traceId = commit.traceId || commit.result?.trace_id || "";
  log("commit", {
    reason,
    codexSessionId: state.codexSessionId,
    ovSessionId,
    archived: commit.result?.archived ?? false,
    taskId: commit.result?.task_id,
    status: commit.result?.status,
    trace_id: traceId || undefined,
  });
  state.ovSessionId = null;
  await saveState(state, { touch: false });
  // Only retire the marker this pass verified; a newer one belongs to an exit
  // that happened while we were committing.
  await clearEnded(
    state.codexSessionId,
    typeof endToken === "number" ? { before: endToken + 1 } : {},
  );
  return { committed: true, ovSessionId, traceId };
}

/**
 * Retire a state file with no live OV session. A cursor that never captured
 * anything carries no resume value, so it goes on the idle schedule; a real
 * cursor is kept until COMMITTED_TTL_MS in case the codex session is resumed.
 */
async function maybeRetireCursorState(state, ageMs) {
  const hasCursor = Number(state.capturedTurnCount) > 0;
  const ttl = hasCursor ? COMMITTED_TTL_MS : IDLE_TTL_MS;
  if (ageMs <= ttl) return false;
  log("state_retire", {
    codexSessionId: state.codexSessionId,
    capturedTurnCount: state.capturedTurnCount,
    ageMs,
    ttlMs: ttl,
  });
  await clearState(state.codexSessionId);
  return true;
}

function describeCommittedSessions(commits) {
  const traceIds = commits.map((item) => item.traceId).filter(Boolean);
  if (commits.length === 1) {
    return `OpenViking session ${commits[0].ovSessionId} is committed` +
      (traceIds.length ? ` (trace_id=${traceIds[0]})` : "");
  }
  return `OpenViking sessions ${commits.map((item) => item.ovSessionId).join(", ")} are committed` +
    (traceIds.length ? ` (trace_ids=${traceIds.join(",")})` : "");
}

async function main() {
  let input;
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    input = JSON.parse(Buffer.concat(chunks).toString());
  } catch {
    log("skip", { stage: "stdin_parse", reason: "invalid input" });
    noop();
    return;
  }

  const source = input.source || "unknown";
  const newSessionId = input.session_id || "unknown";
  const cwd = typeof input.cwd === "string" && input.cwd.trim() ? input.cwd : process.cwd();
  // The workspace layer belongs to the session's directory, which only the
  // payload knows; see loadConfig for why re-resolving this late is safe.
  cfg = loadConfig(cwd);
  const effectivePeer = resolveEffectivePeerId({ cfg, cwd });
  activePeerId = effectivePeer.peerId;
  if (newSessionId !== "unknown") {
    const state = await loadState(newSessionId);
    await saveState({
      ...state,
      workspacePeerId: effectivePeer.source === "workspace" ? effectivePeer.peerId : "",
    });
  }
  log("start", {
    source,
    newSessionId,
    idleTtlMs: IDLE_TTL_MS,
    peerSource: effectivePeer.source,
  });

  try {
    await detectRecallCompressorProfile(cfg, { log, logError });
  } catch (err) {
    logError("compress_profile_detect_uncaught", err);
  }

  if (source === "resume") {
    // Earliest liveness signal after `codex resume`: the thread is running
    // again, so a marker left by its previous exit must not trigger the sweep.
    await clearEnded(newSessionId, { before: HOOK_STARTED_AT });
    const health = await fetchJSON("/health");
    if (!health) {
      logError("health_check", "server unreachable; skipping profile + archive injection");
      noop();
      return;
    }
    const [profileContext, archiveContext] = await Promise.all([
      buildSessionProfileContext(),
      buildResumeArchiveContext(newSessionId),
    ]);
    emitSessionStartOutput({ contexts: [profileContext, archiveContext] });
    return;
  }

  // Other non-startup sources are hard no-ops. We don't sweep there, because
  // reconnect-like sources may fire often and sweep should stay tied to a new
  // session boundary.
  if (source !== "startup" && source !== "clear") {
    log("skip", { stage: "source_check", reason: `source=${source} (only startup|clear act)` });
    noop();
    return;
  }

  const health = await fetchJSON("/health");
  if (!health) {
    logError("health_check", "server unreachable; skipping profile injection + commit + sweep");
    noop();
    return;
  }

  const profileContext = await buildSessionProfileContext();
  const now = Date.now();
  const commits = [];
  let retired = 0;
  let lockSkipped = 0;

  for (const s of await listStates()) {
    if (!s?.codexSessionId) continue;
    if (s.codexSessionId === newSessionId) continue;
    if (typeof s.lastUpdatedAt !== "number") continue;
    const ageMs = now - s.lastUpdatedAt;

    // A marker must always enter the lock, even with no live id: PreCompact
    // releases the id while leaving a cursor behind, so the tail turns of a
    // session whose SessionEnd worker died are only reachable through a
    // catch-up under the lock.
    if (!s.ovSessionId && !s.endedAt) {
      if (await maybeRetireCursorState(s, ageMs)) retired += 1;
      continue;
    }
    if (!s.endedAt && ageMs <= IDLE_TTL_MS) continue;

    const reason = s.endedAt ? "ended_retry" : "idle_ttl";
    log("sweep", { codexSessionId: s.codexSessionId, ovSessionId: s.ovSessionId, ageMs, reason });
    // Try-lock only: a held lock means a SessionEnd or Stop worker is already
    // committing this session (user quit and relaunched within seconds).
    const outcome = await withSessionLock(s.codexSessionId, async ({ heartbeat }) => {
      const fresh = await loadState(s.codexSessionId);

      // Re-read the marker under the lock: it may have been cleared by a
      // resume or replaced by a newer exit since listStates() sampled it.
      const endToken = await readEndedAt(s.codexSessionId);
      if (reason === "ended_retry" && (endToken === undefined || endToken > s.endedAt)) {
        if (ageMs <= IDLE_TTL_MS) {
          log("sweep_skip", {
            codexSessionId: s.codexSessionId,
            reason: endToken === undefined ? "marker cleared under the lock" : "newer end marker",
          });
          return null;
        }
      }

      // Turns the session's own workers never sent would be lost by an
      // archive-now commit, so catch them up first and keep the session live
      // for the next sweep if any of them failed to land.
      const { newTurns, added, skipped, unreadable } = await catchUpTurns({
        state: fresh,
        transcriptPath: fresh.transcriptPath,
        fetchJSONRes,
        activePeerId: fresh.workspacePeerId || activePeerId,
        cfg,
        log,
        logError,
        heartbeat,
      });
      if (added > 0) log("appended_catchup", { ovSessionId: fresh.ovSessionId, added });

      // An unreadable transcript is not an empty one: the tail turns may still
      // be there. Keep the live id and the marker for the next sweep.
      if (unreadable) {
        logError("transcript_unreadable", {
          codexSessionId: s.codexSessionId,
          ovSessionId: fresh.ovSessionId,
          transcriptPath: fresh.transcriptPath,
        });
        await saveState(fresh, { touch: false });
        return null;
      }

      if (newTurns.length > 0 && !skipped && added < newTurns.length) {
        logError("append_incomplete", {
          codexSessionId: s.codexSessionId,
          ovSessionId: fresh.ovSessionId,
          attempted: newTurns.length,
          added,
        });
        await saveState(fresh, { touch: false });
        return null;
      }

      // The catch-up derives a live id whenever it sends something; still
      // having none means there is genuinely nothing to commit, so the marker
      // can go.
      if (!fresh.ovSessionId) {
        if (added > 0) await saveState(fresh, { touch: false });
        await clearEnded(
          s.codexSessionId,
          typeof endToken === "number" ? { before: endToken + 1 } : {},
        );
        log("skip", {
          stage: "commit",
          codexSessionId: s.codexSessionId,
          reason: "no live OV session for this codex session",
        });
        return null;
      }

      return commitAndRelease(fresh, reason, endToken);
    }, { waitMs: 0 });

    if (outcome.skipped) {
      lockSkipped += 1;
      log("sweep_skip", { codexSessionId: s.codexSessionId, reason: "locked by another writer" });
      continue;
    }
    if (outcome.value?.committed) commits.push(outcome.value);
  }

  const ovSessionIds = commits.map((item) => item.ovSessionId);

  log("done", {
    source,
    committed: commits.length,
    retired,
    lockSkipped,
    ovSessionIds,
  });

  if (commits.length > 0) {
    emitSessionStartOutput({
      contexts: [profileContext],
      systemMessage: describeCommittedSessions(commits),
    });
  } else {
    emitSessionStartOutput({ contexts: [profileContext] });
  }
}

main().catch((err) => { logError("uncaught", err); noop(); });
