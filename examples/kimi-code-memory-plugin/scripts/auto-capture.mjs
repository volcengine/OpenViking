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
import {
  planCapture,
  sendPlannedCapture,
  canCommitCapture,
  advanceCapturedSinceCommit,
  shouldQueueSessionEndCommit,
} from "./ov-session.mjs";
import { commitAgentSession, runHookStage } from "./shared/agent-hook-runtime.mjs";
import { extractCaptureTurns } from "./shared/capture-utils.mjs";
import { enqueue, listPending } from "./shared/pending-queue.mjs";
import { createOvHttp } from "./shared/ov-http.mjs";
import { resolveEffectivePeerId } from "./shared/workspace-peer.mjs";

const EVENT = process.argv[2] || "stop";
const { log, logError } = createLogger(`auto-capture:${EVENT}`);

let cfg = loadConfig();
let fetchJSON = createOvHttp(cfg, { defaultTimeoutMs: cfg.timeoutMs });

runHookStage(
  {
    clientId: "kimicode",
    loadConfig: (cwd) => {
      cfg = loadConfig(cwd);
      return cfg;
    },
    gates: { enabled: (reloaded) => reloaded.autoCapture },
    // Capture is a write path: no envelope, no stdout.
    envelope: () => {},
    onSkip: (reason) => log("skip", { stage: "init", reason, event: EVENT }),
  },
  async (stage) => {
    const { input, cwd } = stage;
    cfg = stage.cfg;
    const effectivePeer = resolveEffectivePeerId({ cfg, cwd });
    fetchJSON = createOvHttp(cfg, {
      defaultTimeoutMs: cfg.timeoutMs,
      resolveActorPeerId: () => effectivePeer.peerId,
      requireJsonBody: true,
    });

    const ovSessionId = deriveOvSessionId(stage.sessionId);

    // Overall synchronous budget (spec §5.5): lock wait, message backfill and
    // commit share one deadline, and every OV request below uses the REMAINING
    // time instead of each taking the full per-request timeout. Interrupt gets
    // a tighter budget than its 10s host timeout because the user is waiting
    // on the prompt; overflow parks in the pending queue for SessionStart.
    const BUDGET_MS = EVENT === "interrupt" ? 2000 : 8000;
    const deadline = Date.now() + BUDGET_MS;
    const remainingMs = () => Math.max(250, deadline - Date.now());
    const fitBudget = () => {
      fetchJSON = createOvHttp(cfg, {
        defaultTimeoutMs: Math.min(cfg.timeoutMs, remainingMs()),
        resolveActorPeerId: () => effectivePeer.peerId,
        requireJsonBody: true,
      });
    };

    const locked = await withSessionLock(stage.sessionId, async () => {
      const state = await loadState(stage.sessionId);

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
      if (decoded.protocolVersion && cfg.wireProtocolVersion && decoded.protocolVersion !== cfg.wireProtocolVersion) {
        logError(
          "wire_protocol_drift",
          `wire protocol ${decoded.protocolVersion}, plugin verified against ${cfg.wireProtocolVersion}`,
        );
        return; // §5.7: warn and skip, never silently mis-capture
      }

      // Shape the decoded entries through the shared capture model — the
      // adapter never re-implements sanitize/filter/part-building.
      const turns = extractCaptureTurns(decoded.entries, cfg);
      const plan = planCapture(turns, state.capturedTurnIds || []);
      if (plan.length === 0) {
        log("nothing_new", { event: EVENT, ovSessionId, lastTurnId: state.lastTurnId });
        return;
      }

      const acked = new Set(state.capturedTurnIds || []);
      const previousCaptured = Number(state.capturedSinceCommit || 0);
      let capturedSinceCommit = previousCaptured;

      fitBudget(); // the send spends only what remains of the hook's budget
      const result = await sendPlannedCapture(fetchJSON, ovSessionId, plan, {
        peerId: effectivePeer.peerId,
        onSent: async ({ confirmed, confirmedPayloads }) => {
          for (const key of confirmed) acked.add(key);
          capturedSinceCommit = advanceCapturedSinceCommit(previousCaptured, confirmedPayloads);
          // Persist as we go: a crash mid-send must not re-send confirmed turns.
          state.capturedTurnIds = [...acked];
          state.capturedSinceCommit = capturedSinceCommit;
          await saveState(stage.sessionId, state).catch(() => {});
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
      {
        const keysByTurnId = new Map(
          plan.map((item) => [String(item.turn.turnId ?? ""), item.keys]),
        );
        let advanceTo = null;
        for (const turn of decoded.turns) {
          if (turn.turnId == null) continue;
          const keys = keysByTurnId.get(String(turn.turnId));
          if (keys && !keys.every((key) => acked.has(key))) break;
          advanceTo = turn.turnId;
        }
        if (advanceTo != null) state.lastTurnId = advanceTo;
      }
      await saveState(stage.sessionId, state);

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
        fitBudget(); // the commit gets the remainder, not a fresh timeout
        const committed = await commitAgentSession(fetchJSON, ovSessionId, log);
        if (committed.ok) {
          state.capturedSinceCommit = 0;
          await saveState(stage.sessionId, state);
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
    });
    if (locked === null) {
      // Lock never came: this hook did nothing. Fail open, the next event retries.
      logError("lock_timeout", { event: EVENT, sessionId: stage.sessionId });
    }
  },
).catch((error) => {
  logError(`auto-capture:${EVENT}`, error?.message || String(error));
  process.exit(0); // fail-open; the pending queue owns retries
});
