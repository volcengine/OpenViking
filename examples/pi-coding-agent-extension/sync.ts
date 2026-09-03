import type { OVClient } from "./client.js";
import { appendFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import type { OVConfig } from "./config.js";
import { deriveHarnessSessionId } from "./shared/session-model.mjs";
import { drainPendingForSession, enqueue, replayPending } from "./shared/pending-queue.mjs";
import { extractBranchCapturePayloads } from "./lib/capture-adapter.mjs";
import { estimatePayloadTokens } from "./lib/takeover-core.mjs";

const TAKEOVER_DRAIN_TIME_BUDGET_MS = 1000;

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

function debugLog(message: string): void {
  const file = process.env.OV_DEBUG_LOG;
  if (!file) return;
  try {
    mkdirSync(dirname(file), { recursive: true });
    appendFileSync(file, `${new Date().toISOString()} ${message}\n`);
  } catch {
    // Best effort; logging must never affect pi.
  }
}

export class SyncManager {
  private client: OVClient;
  private config: OVConfig;
  private ovSessionId: string | null = null;
  private syncedEntryCount = 0;

  constructor(client: OVClient, config: OVConfig) {
    this.client = client;
    this.config = config;
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
      (stage: string, data: unknown) =>
        debugLog(`${stage}: ${JSON.stringify(data)}`),
    );
  }

  async flushForTakeover(): Promise<boolean> {
    if (!this.ovSessionId) return false;
    if (!this.client.connected) return false;
    const result = await drainPendingForSession(
      (path: string, init?: any) => this.client.fetchJSON(path, init, 10000),
      (stage: string, data: unknown) =>
        debugLog(`${stage}: ${JSON.stringify(data)}`),
      this.ovSessionId,
      { timeBudgetMs: TAKEOVER_DRAIN_TIME_BUDGET_MS },
    );
    return result.remaining === 0;
  }

  async syncBranch(branch: any[]): Promise<SyncBranchResult> {
    if (!this.ovSessionId) return { added: 0, tokens: 0, allDelivered: true };

    const extracted = extractBranchCapturePayloads(branch, this.syncedEntryCount, this.config);
    if (extracted.resetWatermark) this.syncedEntryCount = 0;
    let added = 0;
    let tokens = 0;
    let allDelivered = true;
    for (const payload of extracted.payloads) {
      const result = await this.addPayload(payload);
      if (!result.accepted) break;
      added++;
      tokens += estimatePayloadTokens(payload);
      allDelivered = allDelivered && result.delivered;
    }
    if (added === extracted.payloads.length) {
      this.syncedEntryCount = extracted.nextEntryCount;
    }
    if (added > 0 && !this.config.takeoverEnabled) {
      await this.commitIfNeeded();
    }
    return { added, tokens, allDelivered };
  }

  async addPayload(payload: any): Promise<AddPayloadResult> {
    if (!this.ovSessionId) return { accepted: false, delivered: false };
    const ok = await this.client.addMessagePayload(this.ovSessionId, payload);
    if (ok) return { accepted: true, delivered: true };
    await enqueue("addMessage", this.ovSessionId, payload);
    return { accepted: true, delivered: false };
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
      debugLog(
        `commit: session=${this.ovSessionId} ok=false status=${response.status ?? 0} ` +
          `trace_id=${response.traceId || "none"} ` +
          `error=${response.error?.message || response.error?.code || "unknown"}`,
      );
      if (opts.queueOnFailure !== false) {
        await enqueue("commitSession", this.ovSessionId, {
          keep_recent_count: opts.keepRecentCount ?? this.config.commitKeepRecentCount,
        });
      }
      return null;
    }
    debugLog(
      `commit: session=${this.ovSessionId} ok=true trace_id=${result.trace_id || "none"}`,
    );
    return result;
  }

  async shutdown(): Promise<void> {
    return;
  }
}
