import type { OVClient } from "./client.js";
import { randomUUID } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
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
  queued: number;
  permanentFailures: number;
}

export class SyncManager {
  private client: OVClient;
  private config: OVConfig;
  private logger: ReturnType<typeof createLogger>;
  private ovSessionId: string | null = null;
  private syncedEntryCount = 0;
  /** Entry at the numeric watermark; detects a same-length sibling branch. */
  private syncedEntryId = "";
  /** Keep queued turns ordered even when two handlers share one clock tick. */
  private lastQueuedAt = 0;
  /**
   * Messages this session lost for good: non-retryable (4xx) rejections and
   * queued entries whose retry budget ran out. They are counted as accepted so
   * the watermark advances and the flush barrier can still clear — but the
   * archive is then missing them, so this count is takeover's capture-gap
   * signal. Takeover persists the resulting boolean with its own session state.
   */
  private droppedForever = 0;
  /** Why the last commit request got no result, for `/viking commit` to show. */
  private lastCommitFailure = "";

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
  /** Messages OpenViking will never receive; they are missing from the archive. */
  get droppedCount(): number { return this.droppedForever; }
  /** `HTTP <status>: <server message>` of the last failed commit; empty after a success. */
  get lastCommitError(): string { return this.lastCommitFailure; }

  /**
   * How many payloads the capture path would actually send for a slice of the
   * branch — the exact `keep_recent_count` the server expects, which is a
   * message count with system/custom/filtered entries excluded, not a user-turn
   * count. Runs the same extraction takeover trims to, from a zero watermark so
   * it measures the slice itself.
   */
  captureCount(branchSlice: any[]): number {
    const extracted = extractBranchCapturePayloads(branchSlice, 0, this.config);
    return extracted.payloads.length;
  }

  restoreWatermark(n: number, branch: any[]): void {
    this.syncedEntryCount = Math.max(0, Math.floor(Number(n) || 0));
    this.syncedEntryId = branch[this.syncedEntryCount - 1]?.id || "";
  }

  async ensureSession(piSessionId: string): Promise<boolean> {
    if (this.ovSessionId) return true;

    const id = deriveHarnessSessionId("pi-", piSessionId);
    this.ovSessionId = id;
    return true;
  }

  async replayPending(): Promise<void> {
    if (!this.client.connected) return;
    if (this.ovSessionId) {
      // The generic replay path intentionally drops 4xx and exhausted entries
      // without reporting which session lost them. Track this session's losses
      // in every mode so neither takeover nor an ordinary commit can hide an
      // incomplete archive.
      await this.drainSessionBacklog();
      // Other sessions' backlog still goes through the generic replay, but only
      // once none of this session's entries are left for it to drop untracked;
      // otherwise it waits for a later start.
      const sid = this.ovSessionId;
      if (sid && countUndeliveredForSession(await listPending(), sid) > 0) return;
      if (sid && await hasProcessingMessage(sid)) return;
    }
    await replayPending(
      (path: string, init?: any) => this.client.fetchJSON(path, init),
      (stage: string, data: unknown) => this.logger.log(stage, data),
    );
  }

  async flushForTakeover(budgetMs?: number): Promise<boolean> {
    if (!this.ovSessionId) return false;
    // Drain is bounded (time / max batches), and takeover passes what is left
    // of its handler budget. Remaining undelivered entries — including any
    // still claimed as `.processing` — keep the barrier closed until a later
    // turn finishes draining them.
    if (this.client.connected) await this.drainSessionBacklog(budgetMs);
    const pending = await listPending();
    if (countUndeliveredForSession(pending, this.ovSessionId) !== 0) return false;
    // listPending intentionally exposes only `.json` entries. A fresh
    // `.processing` file may be owned by another pi process, so absence from
    // that list is not proof of delivery. Conservatively wait until it is
    // dequeued or recovered on a later attempt.
    return !(await hasProcessingMessage(this.ovSessionId));
  }

  /**
   * Replay this session's queued addMessage entries through the batch
   * endpoint, BATCH_LIMIT per request. The shared replayPending() sends one
   * request per entry and stops after one replay window, which is what let a
   * large offline backlog block the takeover barrier for many turns (#4504).
   * Entries are claimed one batch at a time so a failed batch only costs a
   * retry for the entries it contained.
   *
   * Bounded per call via OPENVIKING_PENDING_DRAIN_BUDGET_MS (default 10s) and
   * optional OPENVIKING_PENDING_DRAIN_MAX_BATCHES so a huge backlog cannot
   * block turn_end for an unbounded wall time; remainder drains on later turns.
   * Every caller runs inside a pi event handler, which hosts cap at 30s, so a
   * caller can narrow the budget further with `budgetMs`.
   */
  private async drainSessionBacklog(budgetMs?: number): Promise<void> {
    const sid = this.ovSessionId;
    if (!sid) return;
    const budgetRaw = Number(process.env.OPENVIKING_PENDING_DRAIN_BUDGET_MS);
    const configured = Number.isFinite(budgetRaw) && budgetRaw >= 0 ? budgetRaw : 10_000;
    const timeBudgetMs = Number.isFinite(budgetMs) ? Math.max(0, Math.min(configured, Number(budgetMs))) : configured;
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
      const result = await sendSessionMessages(
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

      // A permanent rejection is removed immediately. Retryable failures stay
      // queued with one more retry; incrementRetry returning false means either
      // retry exhaustion or a queue write failure. Both are permanent gaps.
      for (const { filename, entry } of claimed.slice(delivered)) {
        if (!result.retryable) {
          await dequeue(filename);
          this.droppedForever += 1;
        } else if ((await incrementRetry(filename, entry)) === false) {
          this.droppedForever += 1;
        }
      }
      this.logger.log("drain", {
        session: sid,
        delivered,
        retried: claimed.length - delivered,
      });
      return;
    }
  }

  private fetchJSON = (path: string, init?: any) => this.client.fetchJSON(path, init);

  async syncBranch(branch: any[], opts: { queueOnly?: boolean; autoCommit?: boolean } = {}): Promise<SyncBranchResult> {
    if (!this.ovSessionId) {
      return { added: 0, tokens: 0, allDelivered: true, queued: 0, permanentFailures: 0 };
    }

    const extracted = extractBranchCapturePayloads(
      branch,
      this.syncedEntryCount,
      this.config,
      this.syncedEntryId,
    );
    if (extracted.resetWatermark) {
      this.syncedEntryCount = 0;
      this.syncedEntryId = "";
    }
    const sent = opts.queueOnly
      ? await this.queuePayloads(extracted.payloads, extracted.payloadEntryIds)
      : await this.sendPayloads(extracted.payloads, extracted.payloadEntryIds);
    const added = sent.accepted;
    let tokens = 0;
    for (const payload of extracted.payloads.slice(0, added)) {
      tokens += estimatePayloadTokens(payload);
    }
    const allDelivered = sent.delivered === added && sent.permanentFailures === 0;
    if (added === extracted.payloads.length) {
      this.syncedEntryCount = extracted.nextEntryCount;
      this.syncedEntryId = extracted.nextEntryId;
    }
    if (added > 0 && !this.config.takeoverEnabled && !opts.queueOnly && opts.autoCommit !== false && this.droppedForever === 0) {
      await this.commitIfNeeded();
    }
    return {
      added,
      tokens,
      allDelivered,
      queued: sent.queued,
      permanentFailures: sent.permanentFailures,
    };
  }

  async addPayload(payload: any): Promise<AddPayloadResult> {
    const sent = await this.sendPayloads([payload]);
    return { accepted: sent.accepted === 1, delivered: sent.delivered === 1 };
  }

  private sourceDedupSalts(entryIds: (string | null)[]): string[] {
    return entryIds.map((entryId) => entryId
      ? `pi-entry:${entryId}`
      // A host without stable entry IDs must still preserve repeated turns.
      // It cannot deduplicate retries of an unidentified entry safely.
      : `pi-unidentified:${randomUUID()}`);
  }

  /** Persist a completed turn without sending it ahead of startup replay. */
  private async queuePayloads(payloads: any[], entryIds: (string | null)[]): Promise<{
    accepted: number;
    delivered: number;
    queued: number;
    permanentFailures: number;
  }> {
    if (!this.ovSessionId || payloads.length === 0) {
      return { accepted: 0, delivered: 0, queued: 0, permanentFailures: 0 };
    }
    const createdAt = Math.max(Date.now(), this.lastQueuedAt + 1);
    const dedupSalts = this.sourceDedupSalts(entryIds);
    let queued = 0;
    for (const [index, payload] of payloads.entries()) {
      const entryTime = createdAt + queued;
      const result = await enqueue("addMessage", this.ovSessionId, payload, {
        createdAt: entryTime,
        dedupSalt: dedupSalts[index],
      });
      if (!result.ok) break;
      this.lastQueuedAt = entryTime;
      queued++;
    }
    const permanentFailures = payloads.length - queued;
    if (permanentFailures > 0) {
      this.droppedForever += permanentFailures;
      this.logger.log("queue", {
        session: this.ovSessionId, queued, permanentFailures,
      });
    }
    // Failed writes are counted as accepted so the watermark cannot resend a
    // later payload ahead of the successfully queued prefix. The gap blocks
    // takeover from trimming history that never reached OpenViking.
    return {
      accepted: queued + permanentFailures,
      delivered: 0,
      queued,
      permanentFailures,
    };
  }

  /**
   * Send payloads in one batch request; retryable failures are queued to disk.
   * Returns how many payloads were accepted (sent or queued, always a prefix)
   * and how many of those were delivered to the server.
   */
  private async sendPayloads(payloads: any[], entryIds?: (string | null)[]): Promise<{
    accepted: number;
    delivered: number;
    queued: number;
    permanentFailures: number;
  }> {
    if (!this.ovSessionId || payloads.length === 0) {
      return { accepted: 0, delivered: 0, queued: 0, permanentFailures: 0 };
    }
    const res = await sendSessionMessages(this.fetchJSON, this.ovSessionId, payloads, {
      enqueueOnRetryable: true,
      ...(entryIds ? { dedupSalts: this.sourceDedupSalts(entryIds) } : {}),
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
    // A non-retryable rejection (4xx) drops the remaining payloads, the same
    // outcome replayPending() applies to such entries. Count them as accepted
    // so the watermark still advances past them; otherwise the next turn would
    // re-extract and re-send the payloads that were already delivered. They are
    // gone from the archive for good, so they also count as a capture gap.
    const dropped = (res.retryable ? 0 : res.failed) + res.enqueueFailed;
    if (dropped > 0) this.droppedForever += dropped;
    return {
      accepted: res.sent + res.queued + dropped,
      delivered: res.sent,
      queued: res.queued,
      permanentFailures: dropped,
    };
  }

  async commitIfNeeded(): Promise<void> {
    if (!this.ovSessionId) return;
    const meta = await this.client.getSession(this.ovSessionId);
    const pending = Number(meta?.pending_tokens || 0);
    if (pending >= this.config.commitTokenThreshold) {
      await this.commit();
    }
  }

  async commit(
    opts: { queueOnFailure?: boolean; keepRecentCount?: number; timeoutMs?: number; enableWorkingMemory?: boolean } = {},
  ): Promise<any | null> {
    if (!this.ovSessionId) {
      this.lastCommitFailure = "no OpenViking session yet";
      return null;
    }
    const response = await this.client.commitSessionResponse(
      this.ovSessionId,
      opts.keepRecentCount,
      opts.timeoutMs,
      opts.enableWorkingMemory,
    );
    const result = response.result;
    if (!result) {
      this.lastCommitFailure = describeCommitError(response.status, response.error);
      this.logger.log("commit", {
        session: this.ovSessionId,
        ok: false,
        status: response.status ?? 0,
        trace_id: response.traceId || "none",
        error: response.error?.message || response.error?.code || "unknown",
      });
      if (opts.queueOnFailure !== false) {
        await enqueue("commitSession", this.ovSessionId, {
          keep_recent_count: opts.keepRecentCount ?? 0,
        });
      }
      return null;
    }
    this.lastCommitFailure = "";
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

/** One line for a failed commit request: status (0 = no response) and the server's message. */
export function describeCommitError(status: number | undefined, error: any): string {
  const code = Number(status) || 0;
  const raw = String(error?.message || error?.code || "unknown error").replace(/\s+/g, " ").trim();
  const message = raw.length > 200 ? `${raw.slice(0, 200)}…` : raw;
  return code > 0 ? `HTTP ${code}: ${message}` : `no response: ${message}`;
}

async function hasProcessingMessage(sessionId: string): Promise<boolean> {
  const dir = process.env.OPENVIKING_PENDING_DIR || join(homedir(), ".openviking", "pending");
  let names: string[];
  try {
    names = await readdir(dir);
  } catch {
    return false;
  }
  for (const name of names) {
    if (!name.endsWith(".processing")) continue;
    try {
      const entry = JSON.parse(await readFile(join(dir, name), "utf8"));
      if (entry?.type === "addMessage" && entry.sessionId === sessionId) return true;
    } catch {
      // An unreadable processing entry cannot be attributed reliably. It stays
      // on disk and the regular stale recovery path will handle it later.
    }
  }
  return false;
}
