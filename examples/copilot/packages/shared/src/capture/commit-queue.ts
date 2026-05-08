/**
 * Per-session commit queue.
 *
 * Wraps `OVClient` with the "stream of captured turns + periodic
 * commit" pattern that auto-capture and auto-stop hooks use. The host
 * calls `enqueue(turns)` whenever new turns arrive; the queue:
 *
 *   1. appends the turns to OV (sync — host needs the server to know
 *      about them before the next recall),
 *   2. advances a chars/4 token counter against `commitTokenThreshold`,
 *   3. when the counter crosses the threshold, dispatches a commit
 *      (detached via `runWriteTask` when async=true; awaited inline
 *      otherwise) and resets the counter,
 *   4. logs structured telemetry through the optional debug logger.
 *
 * `flush()` is the explicit force-commit path used by SessionEnd /
 * SubagentStop / PreCompact: it commits regardless of the pending
 * count.
 *
 * Double-commit guard: an in-flight dispatch sets `flushInFlight`,
 * which short-circuits subsequent triggers until the dispatch
 * resolves. Detached dispatches resolve almost immediately (the
 * spawn returns; the worker outlives the parent), so the guard
 * mostly protects against truly racing callers.
 */

import { estimateTokens } from "../recall/rank.js";
import type { DebugLogger } from "../debug/logger.js";
import type { DetachedSpawnOptions } from "../util/async-writer.js";
import { runWriteTask } from "../util/async-writer.js";
import type { OVClient, OVTurn } from "../ov-client.js";

/**
 * Minimal subset of `OVClient` the queue depends on. Typing the
 * dependency this way keeps tests free of `OVClient` construction
 * (no fetch stub needed) and makes it explicit which methods must
 * not change shape without coordinating with the queue.
 */
export type CommitClient = Pick<OVClient, "appendTurns" | "commit">;

export interface CommitQueueOptions {
  /** OpenViking session id (already derived via session/id.ts). */
  sessionId: string;
  client: CommitClient;
  /** Pending-token count at which the queue triggers a commit. */
  threshold: number;
  /** When true, dispatch commits via the detached worker path. */
  async: boolean;
  /**
   * Spawn factory for the detached commit. Required to actually
   * detach; when omitted, the queue falls back to running commits
   * inline even if `async=true` (matches `runWriteTask`'s contract).
   */
  asyncSpawn?: (payload: { sessionId: string; force: boolean }) => DetachedSpawnOptions;
  logger?: DebugLogger;
}

export interface EnqueueResult {
  /** Number of turns successfully appended. */
  appended: number;
  /** True when this enqueue caused a commit to be dispatched. */
  triggeredCommit: boolean;
  /** Pending-token counter after this enqueue. */
  pendingAfter: number;
}

export class CommitQueue {
  private readonly opts: CommitQueueOptions;
  private readonly logger: DebugLogger | undefined;
  private _pendingTokens = 0;
  private flushInFlight = false;

  constructor(opts: CommitQueueOptions) {
    this.opts = opts;
    this.logger = opts.logger?.child("commit-queue");
  }

  /** Tokens accumulated since the last commit. Test/telemetry hook. */
  get pendingTokens(): number {
    return this._pendingTokens;
  }

  /**
   * Append turns to OV. Accumulates tokens and dispatches a commit
   * when the running counter crosses `threshold`. Always resolves;
   * append failures are logged and return triggeredCommit=false.
   */
  async enqueue(turns: OVTurn[]): Promise<EnqueueResult> {
    if (turns.length === 0) {
      return { appended: 0, triggeredCommit: false, pendingAfter: this._pendingTokens };
    }

    const newTokens = sumTurnTokens(turns);
    const appendRes = await this.opts.client.appendTurns(this.opts.sessionId, turns);
    if (!appendRes.ok) {
      this.logger?.log("append_failed", {
        sessionId: this.opts.sessionId,
        message: appendRes.error.message,
      });
      // Do not accumulate tokens for turns that didn't make it onto the
      // server — committing wouldn't archive them anyway.
      return { appended: 0, triggeredCommit: false, pendingAfter: this._pendingTokens };
    }

    this._pendingTokens += newTokens;
    this.logger?.log("appended", {
      sessionId: this.opts.sessionId,
      turns: turns.length,
      newTokens,
      pendingAfter: this._pendingTokens,
    });

    if (this._pendingTokens >= Math.max(0, this.opts.threshold)) {
      const triggered = await this.dispatchCommit({ force: false });
      return {
        appended: turns.length,
        triggeredCommit: triggered,
        pendingAfter: this._pendingTokens,
      };
    }

    return {
      appended: turns.length,
      triggeredCommit: false,
      pendingAfter: this._pendingTokens,
    };
  }

  /**
   * Force a commit regardless of the pending-token counter. Used on
   * SessionEnd / SubagentStop / PreCompact paths so the last window
   * always lands as an archive even when the threshold wasn't hit.
   */
  async flush(): Promise<void> {
    await this.dispatchCommit({ force: true });
  }

  /**
   * Common dispatch path used by both threshold-triggered and forced
   * commits. Handles the in-flight guard, picks async vs sync, resets
   * the pending counter on success, and surfaces failures only via
   * the debug logger (the host's hot path must not see them).
   *
   * Returns true when a commit was actually dispatched, false when
   * the call was suppressed (in-flight guard).
   */
  private async dispatchCommit({ force }: { force: boolean }): Promise<boolean> {
    if (this.flushInFlight) {
      this.logger?.log("commit_suppressed_inflight", { force });
      return false;
    }
    this.flushInFlight = true;
    const tokensAtDispatch = this._pendingTokens;
    // Reset eagerly so concurrent enqueues start from zero again. If
    // the commit fails the data is still on the server (appendTurns
    // already succeeded); the next commit will catch it.
    this._pendingTokens = 0;
    this.logger?.log("commit_dispatching", {
      sessionId: this.opts.sessionId,
      force,
      tokensAtDispatch,
    });

    try {
      await runWriteTask<{ sessionId: string; force: boolean }>({
        async: this.opts.async,
        payload: { sessionId: this.opts.sessionId, force },
        ...(this.opts.asyncSpawn ? { asyncSpawn: this.opts.asyncSpawn } : {}),
        syncHandler: async (payload) => {
          const res = await this.opts.client.commit(payload.sessionId, { force: payload.force });
          if (!res.ok) {
            // runWriteTask catches this and logs; we re-log here too
            // so the queue's child logger surfaces the same context.
            this.logger?.log("commit_failed", {
              sessionId: payload.sessionId,
              message: res.error.message,
            });
            throw new Error(res.error.message);
          }
        },
        ...(this.logger ? { logger: this.logger } : {}),
      });
      this.logger?.log("commit_dispatched", {
        sessionId: this.opts.sessionId,
        force,
        tokensAtDispatch,
      });
      return true;
    } finally {
      this.flushInFlight = false;
    }
  }
}

function sumTurnTokens(turns: OVTurn[]): number {
  let total = 0;
  for (const turn of turns) {
    if (typeof turn.content === "string" && turn.content.length > 0) {
      total += estimateTokens(turn.content);
    }
  }
  return total;
}
