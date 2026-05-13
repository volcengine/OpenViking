#!/usr/bin/env node

/**
 * SessionStart hook for Codex.
 *
 * Triggers (matcher = "clear|startup" in hooks.json):
 *   - source=startup → fresh codex CLI / `/new` / zouk daemon spawn-without-sessionId
 *   - source=clear   → `/clear` (orphans the current process's previous session)
 *   - source=resume  → `/resume` or short reconnect (HARD no-op for commit)
 *
 * Behavior (see DESIGN.md §3 — "SessionStart source=startup, heuristic"):
 *   On `startup` or `clear`, run the active-window heuristic over state files
 *   excluding the new session_id:
 *     - 0 recently-active     → no-op
 *     - 1 recently-active     → commit it (the just-ended session)
 *     - ≥2 recently-active    → skip; rely on idle TTL
 *   "Recently-active" means lastUpdatedAt within ACTIVE_WINDOW_MS (default 2 min).
 *
 *   At the tail (regardless of which branch above ran), run an idle-TTL sweep:
 *   any state file (including the new session_id, but in practice it's just
 *   been created and is fresh) older than IDLE_TTL_MS (default 30 min) gets
 *   committed and cleared. This catches SIGTERM/Ctrl+C/`/exit` exits and
 *   crashes that left state files orphaned.
 *
 * Commit failure handling:
 *   On any /commit failure (OV unreachable, non-2xx, timeout) we DO NOT call
 *   clearState — we keep the state file with ovSessionId still set so the
 *   next sweep retries. A transient OV outage shouldn't lose memory.
 *
 * Output schema accepts {} as a no-op.
 */

import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { clearState, listStates } from "./session-state.mjs";

const cfg = loadConfig();
const { log, logError } = createLogger("session-start");

const ACTIVE_WINDOW_MS = (() => {
  const v = Number(process.env.OPENVIKING_CODEX_ACTIVE_WINDOW_MS);
  return Number.isFinite(v) && v > 0 ? Math.floor(v) : 120_000;
})();

const IDLE_TTL_MS = (() => {
  const v = Number(process.env.OPENVIKING_CODEX_IDLE_TTL_MS);
  return Number.isFinite(v) && v > 0 ? Math.floor(v) : 1_800_000;
})();

function output(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function noop(message) {
  output(message ? { systemMessage: message } : {});
}

async function fetchJSON(path, init = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), cfg.captureTimeoutMs);
  try {
    const headers = { "Content-Type": "application/json" };
    if (cfg.apiKey) {
      headers["Authorization"] = `Bearer ${cfg.apiKey}`;
      headers["X-API-Key"] = cfg.apiKey;
    }
    if (cfg.account) headers["X-OpenViking-Account"] = cfg.account;
    if (cfg.user) headers["X-OpenViking-User"] = cfg.user;
    if (cfg.agentId) headers["X-OpenViking-Agent"] = cfg.agentId;
    const res = await fetch(`${cfg.baseUrl}${path}`, { ...init, headers, signal: controller.signal });
    const body = await res.json().catch(() => null);
    if (!body) return null;
    if (!res.ok || body.status === "error") return null;
    return body.result ?? body;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function countExtracted(commit) {
  if (!commit?.memories_extracted) return 0;
  if (typeof commit.memories_extracted === "number") return commit.memories_extracted;
  if (typeof commit.memories_extracted === "object") {
    return Object.values(commit.memories_extracted).reduce(
      (a, b) => a + (typeof b === "number" ? b : 0),
      0,
    );
  }
  return 0;
}

async function commitOvSession(ovSessionId) {
  if (!ovSessionId) return null;
  return fetchJSON(
    `/api/v1/sessions/${encodeURIComponent(ovSessionId)}/commit`,
    { method: "POST", body: JSON.stringify({}) },
  );
}

/**
 * Commit and clear a single state file. On commit failure, preserve state
 * (don't call clearState) so the next sweep retries.
 *
 * Returns { committed: bool, extracted: number }.
 */
async function commitAndClear(state, reason) {
  if (state.ovSessionId) {
    const commit = await commitOvSession(state.ovSessionId);
    if (!commit) {
      logError("commit_failed_keep_state", {
        reason,
        codexSessionId: state.codexSessionId,
        ovSessionId: state.ovSessionId,
      });
      return { committed: false, extracted: 0 };
    }
    const extracted = countExtracted(commit);
    log("commit", {
      reason,
      codexSessionId: state.codexSessionId,
      ovSessionId: state.ovSessionId,
      extracted,
      archived: commit.archived ?? false,
      taskId: commit.task_id,
      status: commit.status,
    });
    await clearState(state.codexSessionId);
    return { committed: true, extracted };
  }
  // No OV session attached — nothing to commit on the server, but the local
  // state file is still stale and should be removed.
  log("clear_no_ov", { reason, codexSessionId: state.codexSessionId });
  await clearState(state.codexSessionId);
  return { committed: true, extracted: 0 };
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
  log("start", { source, newSessionId, activeWindowMs: ACTIVE_WINDOW_MS, idleTtlMs: IDLE_TTL_MS });

  // resume is a hard no-op — we don't even sweep, because resume re-fires on
  // short reconnects and we'd otherwise sweep on every reconnect blip.
  if (source !== "startup" && source !== "clear") {
    log("skip", { stage: "source_check", reason: `source=${source} (only startup|clear act)` });
    noop();
    return;
  }

  const health = await fetchJSON("/health");
  if (!health) {
    logError("health_check", "server unreachable; skipping commit + sweep");
    noop();
    return;
  }

  const now = Date.now();
  const states = await listStates();

  // -------------------------------------------------------------------------
  // Active-window heuristic (DESIGN.md §3)
  // -------------------------------------------------------------------------
  const otherStates = states.filter(
    (s) => s?.codexSessionId && s.codexSessionId !== newSessionId,
  );

  const recentlyActive = otherStates.filter(
    (s) => typeof s.lastUpdatedAt === "number"
      && (now - s.lastUpdatedAt) <= ACTIVE_WINDOW_MS,
  );

  let heuristicCommitted = 0;
  let heuristicExtracted = 0;
  const skippedSessionIds = new Set();

  if (recentlyActive.length === 0) {
    log("heuristic", { branch: "0_active", action: "noop", otherStates: otherStates.length });
  } else if (recentlyActive.length === 1) {
    const target = recentlyActive[0];
    log("heuristic", {
      branch: "1_active",
      action: "commit",
      codexSessionId: target.codexSessionId,
      ovSessionId: target.ovSessionId,
    });
    const r = await commitAndClear(target, "heuristic_1_active");
    if (r.committed) {
      heuristicCommitted += 1;
      heuristicExtracted += r.extracted;
    }
  } else {
    log("heuristic", {
      branch: ">=2_active",
      action: "skip; rely on idle TTL",
      activeCount: recentlyActive.length,
      activeIds: recentlyActive.map((s) => s.codexSessionId),
    });
    for (const s of recentlyActive) skippedSessionIds.add(s.codexSessionId);
  }

  // -------------------------------------------------------------------------
  // Idle TTL sweep (tail) — applies to ALL state files including ones we just
  // skipped above (≥2 active path). We re-list because the heuristic branch
  // may have removed entries.
  // -------------------------------------------------------------------------
  const postHeuristic = await listStates();
  let idleCommitted = 0;
  let idleExtracted = 0;

  for (const s of postHeuristic) {
    if (!s?.codexSessionId) continue;
    if (typeof s.lastUpdatedAt !== "number") continue;
    if ((now - s.lastUpdatedAt) <= IDLE_TTL_MS) continue;
    log("idle_sweep", {
      codexSessionId: s.codexSessionId,
      ovSessionId: s.ovSessionId,
      ageMs: now - s.lastUpdatedAt,
    });
    const r = await commitAndClear(s, "idle_ttl");
    if (r.committed) {
      idleCommitted += 1;
      idleExtracted += r.extracted;
    }
  }

  const totalCommitted = heuristicCommitted + idleCommitted;
  const totalExtracted = heuristicExtracted + idleExtracted;

  log("done", {
    source,
    heuristicCommitted,
    idleCommitted,
    totalCommitted,
    totalExtracted,
    skipped: [...skippedSessionIds],
  });

  if (totalCommitted > 0) {
    noop(
      `SessionStart(${source}): committed ${totalCommitted} OpenViking session(s) (` +
        `heuristic=${heuristicCommitted}, idle=${idleCommitted}), ` +
        `${totalExtracted} memory item(s) extracted`,
    );
  } else {
    noop();
  }
}

main().catch((err) => { logError("uncaught", err); noop(); });
