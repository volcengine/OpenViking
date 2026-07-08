import type { OVClient } from "./client.js";
import type { OVConfig } from "./config.js";
import { deriveHarnessSessionId } from "./shared/session-model.mjs";
import { enqueue, replayPending } from "./shared/pending-queue.mjs";
import { extractBranchCapturePayloads } from "./lib/capture-adapter.mjs";

// --- SyncManager ---

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
      () => {},
    );
  }

  async syncBranch(branch: any[]): Promise<number> {
    if (!this.ovSessionId) return 0;

    const extracted = extractBranchCapturePayloads(branch, this.syncedEntryCount, this.config);
    if (extracted.resetWatermark) this.syncedEntryCount = 0;
    let added = 0;
    for (const payload of extracted.payloads) {
      const ok = await this.addPayload(payload);
      if (!ok) break;
      added++;
    }
    if (added === extracted.payloads.length) {
      this.syncedEntryCount = extracted.nextEntryCount;
    }
    if (added > 0) {
      await this.commitIfNeeded();
    }
    return added;
  }

  async addPayload(payload: any): Promise<boolean> {
    if (!this.ovSessionId) return false;
    const ok = await this.client.addMessagePayload(this.ovSessionId, payload);
    if (ok) return true;
    await enqueue("addMessage", this.ovSessionId, payload);
    return true;
  }

  async commitIfNeeded(): Promise<void> {
    if (!this.ovSessionId) return;
    const meta = await this.client.getSession(this.ovSessionId);
    const pending = Number(meta?.pending_tokens || 0);
    if (pending >= this.config.commitTokenThreshold) {
      await this.commit();
    }
  }

  async commit(): Promise<any | null> {
    if (!this.ovSessionId) return null;
    const result = await this.client.commitSession(this.ovSessionId);
    if (!result) {
      await enqueue("commitSession", this.ovSessionId, {
        keep_recent_count: this.config.commitKeepRecentCount,
      });
      return null;
    }
    return result;
  }

  async shutdown(): Promise<void> {
    return;
  }
}
