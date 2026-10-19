#!/usr/bin/env node

/**
 * Capture hook for Kimi Code (Stop / PreCompact / SessionEnd / Interrupt).
 *
 * One dispatcher entry, four subcommands (the previous adapter's proven
 * pattern): `stop`, `pre-compact`, `session-end`, `interrupt`.
 *
 * Pipeline (DESIGN.md §5/§6):
 *   stdin payload → resolve wire.jsonl (session_index.jsonl) → branch-chain
 *   decode (wire-tree) → shared capture shaping (capture-utils) → add-message
 *   payloads → batch send → ack-set cursor advances ONLY on server
 *   confirmation → threshold commit.
 *
 * Windows policy (DESIGN.md §7): NO detached background workers. Kimi plugin
 * hooks run with cwd = plugin root and Windows has no process groups, so a
 * detached writer would die with its parent. Everything runs synchronously
 * inside the hook timeout; a retryable failure parks the unsent payloads in
 * the shared pending queue for the SessionStart replay.
 *
 * Stop's payload carries no assistant text (kimi-code issue #952, open) —
 * the wire.jsonl decode is the only source of the assistant side.
 */

import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { buildKimicodeTurns } from "./kimicode-transcript.mjs";
import { deriveOvSessionId, loadState, saveState, withSessionLock } from "./session-state.mjs";
import { createRemainingMs, boundFetch } from "./hook-budget.mjs";
import {
  planCapture,
  sendPlannedCapture,
  canCommitCapture,
  advanceCapturedSinceCommit,
  nextCursor,
  shouldQueueSessionEndCommit,
} from "./ov-session.mjs";
import { commitAgentSession, runHookStage } from "./shared/agent-hook-runtime.mjs";
import { shapeKimicodeTurns } from "./capture-shape.mjs";
import { isCaptureEnabled } from "./shared/capture-utils.mjs";
import { enqueue, listPending } from "./shared/pending-queue.mjs";
import { createOvHttp } from "./shared/ov-http.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";

const EVENTS = new Set(["stop", "pre-compact", "session-end", "interrupt"]);
const EVENT = process.argv[2] || "stop";
const { log, logError } = createLogger(`auto-capture:${EVENT}`);

// An unknown subcommand is a wiring mistake, not a Stop: treating it as one
// would skip the SessionEnd commit and the Interrupt budget silently.
if (!EVENTS.has(EVENT)) {
  logError("auto-capture", `unknown event: ${EVENT}`);
  process.exit(0);
}

let cfg;
let fetchJSON;

/**
 * Ack keys at or before the cursor are dead weight: the decoder filters by turn
 * id rather than by position, so a truncated journal cannot bring those turns
 * back. Keeping them grows the state file by one entry per message part, and it
 * is rewritten on every confirmation.
 */
function pruneAckedKeys(keys, lastTurnId) {
  const cursor = Number(lastTurnId);
  if (!Number.isFinite(cursor)) return keys;
  return keys.filter((key) => {
    const turnId = Number(String(key).split(":")[0]);
    return !Number.isFinite(turnId) || turnId > cursor;
  });
}

runHookStage(
  {
    clientId: "kimicode",
    loadConfig: (cwd) => {
      cfg = loadConfig(cwd);
      return cfg;
    },
    // The adapter gate and the shared one: the shared predicate is what every
    // host ends up consulting, so a future knob cannot be honoured here alone.
    gates: { enabled: (reloaded) => reloaded.autoCapture && isCaptureEnabled(reloaded) },
    // Capture is a write path: no envelope, no stdout.
    envelope: () => {},
    onSkip: (reason) => log("skip", { stage: "init", reason, event: EVENT }),
  },
  async (stage) => {
    const { input, cwd } = stage;
    cfg = stage.cfg;
    const effectivePeer = resolveEffectivePeerId({ cfg, cwd });

    if (!stage.sessionId) {
      logError("no_session_id", `event=${EVENT}`);
      return;
    }
    const ovSessionId = deriveOvSessionId(stage.sessionId);

    // Overall synchronous budget (spec §5.5): lock wait, message backfill and
    // commit share one deadline, and every OV request below uses the REMAINING
    // time instead of each taking the full per-request timeout. Interrupt gets
    // a tighter budget than its 10s host timeout because the user is waiting
    // on the prompt; overflow parks in the pending queue for SessionStart.
    const BUDGET_MS = EVENT === "interrupt" ? 2000 : 8000;
    const remainingMs = createRemainingMs(BUDGET_MS);
    fetchJSON = boundFetch(
      createOvHttp(cfg, {
        defaultTimeoutMs: cfg.timeoutMs,
        resolveActorPeerId: () => effectivePeer.peerId,
        requireJsonBody: true,
      }),
      remainingMs,
      cfg.timeoutMs,
    );

    // The lock waits inside the same budget as the requests: an interrupt has
    // 2s total, and a lock wait that ignores it would spend the whole hook
    // queueing nothing.
    const locked = await withSessionLock(stage.sessionId, async () => {
      const state = await loadState(stage.sessionId, (message) => logError("state_read_failed", message));

      const decoded = buildKimicodeTurns(input, state);
      if (decoded.status === "missing") {
        log("wire_missing", { event: EVENT, ovSessionId });
        return;
      }
      if (decoded.status !== "ok") {
        // unreadable / tree_error: never guess (§5.4, §5.5). The pending queue
        // and the next hook own the retry.
        logError("wire_unusable", `status=${decoded.status} error=${decoded.error || ""}`);
        return;
      }
      if (decoded.branchDiagnostics?.malformedSwitchLines?.length) {
        log("branch_diagnostics", { malformed: decoded.branchDiagnostics.malformedSwitchLines });
      }
      if (decoded.unknownTypes?.length) {
        log("unknown_loop_event_types", { types: decoded.unknownTypes });
      }
      if (decoded.unresolvedResults || decoded.lateResults) {
        // Dropped tool evidence is a diagnostic, not silence (§7).
        log("dropped_tool_results", {
          unresolved: decoded.unresolvedResults,
          late: decoded.lateResults,
        });
      }
      if (decoded.protocolVersion && cfg.wireProtocolVersion && decoded.protocolVersion !== cfg.wireProtocolVersion) {
        logError(
          "wire_protocol_drift",
          `wire protocol ${decoded.protocolVersion}, plugin verified against ${cfg.wireProtocolVersion}`,
        );
        return; // §5.7: warn and skip, never silently mis-capture
      }

      // Shape the decoded entries through the shared capture model — the
      // adapter never re-implements sanitize/filter/part-building — then apply
      // this host's turn id, envelope and cap rules (capture-shape.mjs).
      const turns = shapeKimicodeTurns(decoded.entries, cfg);
      const plan = planCapture(turns, state.capturedTurnIds || []);
      if (plan.length === 0) {
        // Not an exit: every turn may already be captured, and SessionEnd still
        // has to commit what earlier hooks left uncommitted. The commit gate
        // below decides, and a run with nothing to send is strand-free.
        log("nothing_new", { event: EVENT, ovSessionId, lastTurnId: state.lastTurnId });
      }

      const acked = new Set(state.capturedTurnIds || []);
      const previousCaptured = Number(state.capturedSinceCommit || 0);
      let capturedSinceCommit = previousCaptured;

      const result = await sendPlannedCapture(fetchJSON, ovSessionId, plan, {
        peerId: effectivePeer.peerId,
        onSent: async ({ confirmed, confirmedPayloads }) => {
          for (const key of confirmed) acked.add(key);
          capturedSinceCommit = advanceCapturedSinceCommit(previousCaptured, confirmedPayloads);
          // Persist as we go: a crash mid-send must not re-send confirmed turns.
          state.capturedTurnIds = [...acked];
          state.capturedSinceCommit = capturedSinceCommit;
          try {
            await saveState(stage.sessionId, state);
          } catch (error) {
            logError("state_write_failed", error?.message || String(error));
          }
        },
      });

      log("sent", {
        event: EVENT,
        ovSessionId,
        planned: plan.length,
        sent: result.sent,
        queued: result.queued,
        failed: result.failed,
        enqueueFailed: result.enqueueFailed,
      });

      // The cursor only ever moves to what the server confirmed; a failure
      // leaves the rest for the pending queue and the next hook.
      state.capturedTurnIds = [...acked];
      state.capturedSinceCommit = capturedSinceCommit;
      // lastTurnId gates the NEXT decode (kimicode-transcript skips turns at
      // or before it), so it may only anchor at the last turn whose keys are
      // all acked. Anchoring past an unacked turn would silence it: it is
      // neither in the ack set nor decoded again.
      const advanceTo = nextCursor(plan, acked, decoded.turnOrder);
      if (advanceTo != null) state.lastTurnId = advanceTo;
      state.capturedTurnIds = pruneAckedKeys(state.capturedTurnIds, state.lastTurnId);
      try {
        await saveState(stage.sessionId, state);
      } catch (error) {
        logError("state_write_failed", `after-send: ${error?.message || String(error)}`);
      }

      // Commit only when nothing is stranded: `failed` payloads were rejected
      // and exist nowhere, queued ones are still on disk, and an older queue
      // entry for this session would be archived across. A skipped commit
      // keeps capturedSinceCommit, so a later event commits once the queue
      // drains (§5.5: commit must not cross unreplayed messages).
      const pendingForSession = (await listPending())
        .filter(({ entry }) => entry?.sessionId === ovSessionId);
      const pendingCount = pendingForSession.length;
      const stranded = result.failed > 0 || result.queued > 0 || result.enqueueFailed > 0;
      if (canCommitCapture(result, { event: EVENT, cfg, previousCaptured, pendingCount })) {
        const committed = await commitAgentSession(fetchJSON, ovSessionId, log);
        if (committed.ok) {
          state.capturedSinceCommit = 0;
          try {
            await saveState(stage.sessionId, state);
          } catch (error) {
            logError("state_write_failed", `after-commit: ${error?.message || String(error)}`);
          }
          log("committed", { event: EVENT, ovSessionId });
        } else {
          logError("commit_failed", `status=${committed.status || "unknown"}`);
        }
      } else if (stranded || pendingCount > 0) {
        log("commit_deferred", {
          event: EVENT,
          ovSessionId,
          failed: result.failed,
          queued: result.queued,
          enqueueFailed: result.enqueueFailed,
          pendingCount,
        });
        // SessionEnd is the last chance to close the session. With messages
        // parked on disk, queue the commit behind them (the replay drains
        // addMessage before commitSession); lost payloads suppress this.
        if (shouldQueueSessionEndCommit(EVENT, result, pendingCount)) {
          const lastCreatedAt = pendingForSession.reduce(
            (max, { entry }) => Math.max(max, Number(entry.createdAt) || 0),
            0,
          );
          const queuedCommit = await enqueue("commitSession", ovSessionId, {}, {
            createdAt: lastCreatedAt + 1,
          });
          log("commit_queued", { ok: queuedCommit.ok, deduped: Boolean(queuedCommit.deduped) });
        }
      }
    }, { waitMs: remainingMs() });
    if (locked === null) {
      // Lock never came: this hook did nothing. Fail open, the next event retries.
      logError("lock_timeout", `event=${EVENT} sessionId=${stage.sessionId}`);
    }
  },
).catch((error) => {
  logError(`auto-capture:${EVENT}`, error?.message || String(error));
  process.exit(0); // fail-open; the pending queue owns retries
});
