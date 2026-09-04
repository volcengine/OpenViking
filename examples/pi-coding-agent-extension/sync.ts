import type { OVClient } from "./client.js";
import { createLogger } from "./shared/debug-log.mjs";
import type { OVConfig } from "./config.js";
import { deriveHarnessSessionId } from "./shared/session-model.mjs";
import { claimForReplay, dequeue, enqueue, incrementRetry, listPending, replayPending } from "./shared/pending-queue.mjs";
import { BATCH_LIMIT, sendSessionMessages } from "./shared/batch-send.mjs";
import { extractBranchCapturePayloads } from "./lib/capture-adapter.mjs";
import { countUndeliveredForSession, estimatePayloadTokens } from "./lib/takeover-core.mjs";

// --- SyncManager ---

export interface AddPayloadResult {
  accepted: boolean;
  delivered: boolean;
}

export interface SyncBranchResult {
  added: number;
  tokens: number;
  allDelivered: boolean;
}

export class SyncManager {
  private client: OVClient;
  private config: OVConfig;
  private logger: ReturnType<typeof createLogger>;
  private ovSessionId: string | null = null;
  private syncedEntryCount = 0;

  constructor(client: OVClient, config: OVConfig) {
    this.client = client;
    this.config = config;
    this.logger = createLogger("pi", {
      debug: Boolean(config.debugLogPath),
      debugLogPath: config.debugLogPath,
    });
  }

  get sessionId(): string | null { return this.ovSessionId; }
  get syncedCount(): number { return this.syncedEntryCount; }

  restoreWatermark(n: number): void {
    const next = Math.max(0, Math.floor(Number(n) || 0));
    this.syncedEntryCount = next;
  }

  async ensureSession(piSessionId: string): Promise<boolean> {
    if (this.ovSessionId) return true;

    const id = deriveHarnessSessionId("pi-", piSessionId);
    this.ovSessionId = id;
    return true;
  }

  async replayPending(): Promise<void> {
    if (!this.client.connected) return;
    await replayPending(
      (path: string, init?: any) => this.client.fetchJSON(path, init, 10000),
      (stage: string, data: unknown) => this.logger.log(stage, data),
    );
  }

  async flushForTakeover(): Promise<boolean> {
    if (!this.ovSessionId) return false;
    // Drain is bounded (time / max batches). Remaining undelivered entries —
    // including any still claimed as `.processing` — keep the barrier closed
    // until a later turn finishes draining them.
    if (this.client.connected) await this.drainSessionBacklog();
    const pending = await listPending();
    return countUndeliveredForSession(pending, this.ovSessionId) === 0;
  }

  /**
   * Replay this session's queued addMessage entries through the batch
   * endpoint, BATCH_LIMIT per request. The shared replayPending() sends one
   * request per entry and stops after one replay window, which is what let a
   * large offline backlog block the takeover barrier for many turns (#4504).
   * Entries are claimed one batch at a time so a failed batch only costs a
   * retry for the entries it contained.
   *
   * Bounded per call via OPENVIKING_PENDING_DRAIN_BUDGET_MS (default 60s) and
   * optional OPENVIKING_PENDING_DRAIN_MAX_BATCHES so a huge backlog cannot
   * block turn_end for an unbounded wall time; remainder drains on later turns.
   */
  private async drainSessionBacklog(): Promise<void> {
    const sid = this.ovSessionId;
    if (!sid) return;
    const budgetRaw = Number(process.env.OPENVIKING_PENDING_DRAIN_BUDGET_MS);
    const timeBudgetMs = Number.isFinite(budgetRaw) && budgetRaw >= 0 ? budgetRaw : 60_000;
    const maxRaw = Number(process.env.OPENVIKING_PENDING_DRAIN_MAX_BATCHES);
    const maxBatches =
      Number.isFinite(maxRaw) && maxRaw > 0 ? Math.floor(maxRaw) : Number.POSITIVE_INFINITY;
    const started = Date.now();
    let batches = 0;

    const backlog = (await listPending()).filter(
      ({ entry }) => entry?.type === "addMessage" && entry.sessionId === sid,
    );
    for (let start = 0; start < backlog.length; start += BATCH_LIMIT) {
      if (batches >= maxBatches || Date.now() - started >= timeBudgetMs) {
        this.logger.log("drain", {
          session: sid,
          stopped: batches >= maxBatches ? "max-batches" : "time-budget",
          batches,
          remaining: backlog.length - start,
          elapsedMs: Date.now() - started,
        });
        return;
      }

      const claimed: Array<{ filename: string; entry: any }> = [];
      for (const { filename, entry } of backlog.slice(start, start + BATCH_LIMIT)) {
        const name = await claimForReplay(filename);
        if (name) claimed.push({ filename: name, entry });
      }
      if (claimed.length === 0) continue;
      batches += 1;

      let delivered = 0;
      await sendSessionMessages(
        this.fetchJSON,
        sid,
        claimed.map(({ entry }) => entry.payload),
        {
          onSent: async (count: number) => {
            for (let i = 0; i < count; i++) await dequeue(claimed[delivered++].filename);
          },
        },
      );
      if (delivered === claimed.length) continue;

      // Undelivered entries stay queued with one more retry; incrementRetry
      // drops them once the retry budget is exhausted.
      for (const { filename, entry } of claimed.slice(delivered)) {
        await incrementRetry(filename, entry);
      }
      this.logger.log("drain", {
        session: sid,
        delivered,
        retried: claimed.length - delivered,
      });
      return;
    }
  }

  private fetchJSON = (path: string, init?: any) => this.client.fetchJSON(path, init, 10000);

  async syncBranch(branch: any[]): Promise<SyncBranchResult> {
    if (!this.ovSessionId) return { added: 0, tokens: 0, allDelivered: true };

    const extracted = extractBranchCapturePayloads(branch, this.syncedEntryCount, this.config);
    if (extracted.resetWatermark) this.syncedEntryCount = 0;
    const sent = await this.sendPayloads(extracted.payloads);
    const added = sent.accepted;
    let tokens = 0;
    for (const payload of extracted.payloads.slice(0, added)) {
      tokens += estimatePayloadTokens(payload);
    }
    const allDelivered = sent.delivered === added;
    if (added === extracted.payloads.length) {
      this.syncedEntryCount = extracted.nextEntryCount;
    }
    if (added > 0 && !this.config.takeoverEnabled) {
      await this.commitIfNeeded();
    }
    return { added, tokens, allDelivered };
  }

  async addPayload(payload: any): Promise<AddPayloadResult> {
    const sent = await this.sendPayloads([payload]);
    return { accepted: sent.accepted === 1, delivered: sent.delivered === 1 };
  }

  /**
   * Send payloads in one batch request; retryable failures are queued to disk.
   * Returns how many payloads were accepted (sent or queued, always a prefix)
   * and how many of those were delivered to the server.
   */
  private async sendPayloads(payloads: any[]): Promise<{ accepted: number; delivered: number }> {
    if (!this.ovSessionId || payloads.length === 0) return { accepted: 0, delivered: 0 };
    const res = await sendSessionMessages(this.fetchJSON, this.ovSessionId, payloads, {
      enqueueOnRetryable: true,
    });
    if (res.failed > 0 || res.enqueueFailed > 0) {
      this.logger.log("send", {
        session: this.ovSessionId,
        sent: res.sent,
        queued: res.queued,
        failed: res.failed,
        enqueueFailed: res.enqueueFailed,
        error: res.lastError?.message || res.lastError?.code || "unknown",
      });
    }
    return { accepted: res.sent + res.queued, delivered: res.sent };
  }

  async commitIfNeeded(): Promise<void> {
    if (!this.ovSessionId) return;
    const meta = await this.client.getSession(this.ovSessionId);
    const pending = Number(meta?.pending_tokens || 0);
    if (pending >= this.config.commitTokenThreshold) {
      await this.commit();
    }
  }

  async commit(opts: { queueOnFailure?: boolean; keepRecentCount?: number } = {}): Promise<any | null> {
    if (!this.ovSessionId) return null;
    const response = await this.client.commitSessionResponse(
      this.ovSessionId,
      opts.keepRecentCount,
    );
    const result = response.result;
    if (!result) {
      this.logger.log("commit", {
        session: this.ovSessionId,
        ok: false,
        status: response.status ?? 0,
        trace_id: response.traceId || "none",
        error: response.error?.message || response.error?.code || "unknown",
      });
      if (opts.queueOnFailure !== false) {
        await enqueue("commitSession", this.ovSessionId, {
          keep_recent_count: opts.keepRecentCount ?? this.config.commitKeepRecentCount,
        });
      }
      return null;
    }
    this.logger.log("commit", {
      session: this.ovSessionId,
      ok: true,
      trace_id: result.trace_id || "none",
    });
    return result;
  }

  async shutdown(): Promise<void> {
    return;
  }
}
