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
 *
 * Structure mirrors examples/codex-memory-plugin/scripts/auto-capture.mjs.
 */

import { loadConfig } from "./config.mjs";
import { createLogger } from "./debug-log.mjs";
import { buildKimicodeTurns } from "./kimicode-transcript.mjs";
import { deriveOvSessionId, loadState, saveState, withSessionLock } from "./session-state.mjs";
import { planCapture, sendPlannedCapture, shouldCommit } from "./ov-session.mjs";
import { commitAgentSession, runHookStage } from "./shared/agent-hook-runtime.mjs";
import { extractCaptureTurns } from "./shared/capture-utils.mjs";
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

    await withSessionLock(stage.sessionId, async () => {
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

      const result = await sendPlannedCapture(fetchJSON, ovSessionId, plan, {
        peerId: effectivePeer.peerId,
        onSent: async ({ confirmed }) => {
          for (const key of confirmed) acked.add(key);
          capturedSinceCommit += confirmed.length;
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
      if (decoded.turns.length) {
        state.lastTurnId = decoded.turns[decoded.turns.length - 1].turnId ?? state.lastTurnId;
      }
      await saveState(stage.sessionId, state);

      if (result.sent > 0 && shouldCommit(EVENT, cfg, previousCaptured, result.sent)) {
        const committed = await commitAgentSession(fetchJSON, ovSessionId, log);
        if (committed.ok) {
          state.capturedSinceCommit = 0;
          await saveState(stage.sessionId, state);
          log("committed", { event: EVENT, ovSessionId });
        } else {
          logError("commit_failed", `status=${committed.status || "unknown"}`);
        }
      }
    });
  },
).catch((error) => {
  logError(`auto-capture:${EVENT}`, error?.message || String(error));
  process.exit(0); // fail-open; the pending queue owns retries
});
