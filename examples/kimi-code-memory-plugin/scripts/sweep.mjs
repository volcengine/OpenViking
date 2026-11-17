/**
 * The exit-compensation sweep.
 *
 * `SessionEnd` writes an ended marker and commits what it can, but the host can
 * be killed, and an agent that never drained leaves a turn without
 * `turn.ended`. This sweep is the only place that may close such a turn, and it
 * may do so only under three conditions at once:
 *
 *   1. the state carries an `endedMarker`, so the session really did end;
 *   2. this process holds the session's lock;
 *   3. the wire file's size and mtime are unchanged across two reads.
 *
 * Without (1) a crash tail would be sealed as if the host had finished, losing
 * whatever the user was still doing. Without (3) a turn still being written
 * would be closed mid-step. So a candidate that fails (3) is left for the next
 * start; it is never force-closed.
 *
 * The sweep is bounded: at most `MAX_SWEEP_STATES` state files, oldest marker
 * first, and at most one commit per run. A large backlog drains over several
 * starts rather than holding a hook open.
 */

import { readdir } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

import { makeAgentFetchJSON, readHookStateDetailed, withAgentHookLock } from "./shared/agent-hook-runtime.mjs";
import { captureSession, fileIsQuiet } from "./capture-session.mjs";
import { HARNESS } from "./config.mjs";
import { agentSettled, isSweepCandidate, settleEndedMarker } from "./session-state.mjs";
import { saveState } from "./hook-stage.mjs";

/** How many state files one sweep will look at. */
export const MAX_SWEEP_STATES = 50;

/** How long the sweep waits between its two stat calls. */
export const SWEEP_SETTLE_MS = 250;

function stateRoot() {
  return process.env.OPENVIKING_HOOK_STATE_DIR || join(homedir(), ".openviking", "hook-state");
}

/**
 * Sweep ended sessions.
 *
 * Returns counters, not a verdict: `{scanned, candidates, swept, skipped,
 * committed, failed}`. `skipped` means "a condition was not met yet", which is
 * normal and must not be reported as an error.
 */
export async function runStartupSweep({
  cfg,
  cwd,
  log,
  logError,
  deadline,
  fetchJSON,
  excludeSessionId = "",
}) {
  const counters = { scanned: 0, candidates: 0, swept: 0, skipped: 0, committed: 0, failed: 0 };
  const dir = join(stateRoot(), HARNESS);

  let files;
  try {
    files = (await readdir(dir)).filter((name) => name.endsWith(".json"));
  } catch {
    return counters;
  }

  const candidates = [];
  for (const name of files) {
    if (counters.scanned >= MAX_SWEEP_STATES) break;
    if (Date.now() >= deadline) break;
    const nativeSessionId = name.slice(0, -".json".length);
    if (excludeSessionId && nativeSessionId === excludeSessionId) continue;
    counters.scanned += 1;
    const read = await readHookStateDetailed(HARNESS, nativeSessionId);
    if (read.status !== "ok") continue;
    if (!isSweepCandidate(read.state)) continue;
    counters.candidates += 1;
    // Only the ordering key is kept; the state itself is re-read under the lock.
    candidates.push({ nativeSessionId, marker: read.state.endedMarker.at });
  }

  candidates.sort((a, b) => a.marker - b.marker);

  for (const candidate of candidates) {
    if (Date.now() >= deadline) break;
    // One commit per sweep is a global limit. It is checked here and the loop
    // stops rather than continuing to inspect sessions it may not commit.
    if (counters.committed >= 1) {
      // The candidates this loop never reached are the remainder after the ones
      // it did — swept, failed *and* already skipped. Subtracting only the
      // first two double-counts every earlier skip.
      const remaining = candidates.length - (counters.swept + counters.failed + counters.skipped);
      counters.skipped += Math.max(0, remaining);
      break;
    }
    // One bad session must not stop the rest. Candidates are ordered oldest
    // marker first, so a session that throws on every sweep would otherwise
    // starve every candidate behind it and compensation would never advance.
    let outcome;
    try {
      outcome = await sweepOne({
        candidate,
        cfg,
        cwd,
        log,
        logError,
        deadline,
        fetchJSON,
      });
    } catch (error) {
      counters.failed += 1;
      log("sweep_failed", { sessionId: candidate.nativeSessionId, error: error?.message || String(error) });
      logError("sweep_failed", error);
      continue;
    }
    if (outcome === "swept") counters.swept += 1;
    else if (outcome === "committed") {
      counters.swept += 1;
      counters.committed += 1;
    } else if (outcome === "failed") counters.failed += 1;
    else counters.skipped += 1;
  }

  return counters;
}

/**
 * Sweep one ended session.
 *
 * The ended marker is what makes an open turn a *recovery partial* rather than
 * an invented ending: the turn's existing content is sent, and nothing is
 * synthesized — no assistant text, no tool result.
 */
async function sweepOne({ candidate, cfg, cwd, log, logError, deadline, fetchJSON }) {
  const { nativeSessionId } = candidate;

  // Everything below happens under the session's lock, and everything is
  // re-established inside it. The state and the file quietness read before the
  // lock are stale by definition: another hook may have written or resumed in
  // between, and sealing a session that just came back to life is the one
  // mistake this sweep must not make.
  const result = await withSweepLock(nativeSessionId, deadline, async () => {
    const read = await readHookStateDetailed(HARNESS, nativeSessionId);
    if (read.status !== "ok") return { outcome: "skipped", reason: `state_${read.status}` };
    const state = read.state;
    if (!isSweepCandidate(state)) return { outcome: "skipped", reason: "no_longer_a_candidate" };
    // A resume that happened while this sweep was queued invalidates it.
    if (typeof state.resumedAt === "number" && state.resumedAt > (state.endedMarker?.at || 0)) {
      return { outcome: "skipped", reason: "resumed" };
    }

    // The session's own directory, from when it last ran, is the only correct
    // context for its configuration and peer: the sweep is started by another
    // project's hook, and that project is not this session's workspace.
    const sessionCwd = state.workspaceCwd || cwd;
    if (!sessionCwd) return { outcome: "skipped", reason: "no_workspace" };
    const { resolveConfigForCwd } = await import("./config.mjs");
    const sweepCfg = resolveConfigForCwd(sessionCwd, { base: cfg }) || cfg;

    const quiet = await isWireQuiet(state, { deadline });
    if (!quiet) return { outcome: "skipped", reason: "wire_busy" };

    const { fetchJSON: sweepFetch, effectivePeer } = makeAgentFetchJSON(sweepCfg, sessionCwd, {
      defaultTimeoutMs: Math.max(500, deadline - Date.now()),
      getRemainingMs: () => deadline - Date.now(),
    });

    const capture = await captureSession({
      cfg: sweepCfg,
      input: {},
      cwd: sessionCwd,
      log,
      logError,
      deadline,
      fetchJSON: sweepFetch,
      peerId: effectivePeer?.peerId || "",
      sessionId: nativeSessionId,
      state: { ...state, endedMarkerSeen: true },
      mode: "recovery",
      now: Date.now(),
    });

    const agents = Object.values(capture.state.agents || {});
    const allSettled = agents.length > 0 && agents.every(agentSettled);
    const partialLoss = agents.some((agent) => (agent.pendingLost || []).some((item) => item.accepted));

    const settled = settleEndedMarker(capture.state, {
      allAgentsSettled: allSettled,
      partialLoss,
      at: Date.now(),
    });
    await saveState(nativeSessionId, settled);
    return {
      outcome: capture.summary.committed > 0 ? "committed" : (allSettled ? "swept" : "skipped"),
      allSettled,
    };
  });

  if (result === null) return "skipped";
  if (result.outcome === "skipped") log("sweep_skip", { sessionId: nativeSessionId, reason: result.reason });
  return result.outcome;
}

async function isWireQuiet(state, { deadline }) {
  for (const agent of Object.values(state.agents || {})) {
    if (!agent.wirePath) continue;
    const quiet = await fileIsQuiet(agent.wirePath, {
      settleMs: SWEEP_SETTLE_MS,
      deadline,
    });
    if (!quiet) return false;
  }
  return true;
}

async function withSweepLock(nativeSessionId, deadline, run) {
  // The sweep runs inside a SessionStart's budget, so its lock wait is capped
  // by what is left of that budget rather than by a fixed two seconds.
  const waitMs = Number.isFinite(deadline) ? Math.max(0, Math.min(2000, deadline - Date.now())) : 2000;
  if (waitMs <= 0) return null;
  return withAgentHookLock(HARNESS, nativeSessionId, run, { waitMs });
}
