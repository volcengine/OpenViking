import type { OVClient } from "./client.js";
import type { OVConfig } from "./config.js";
import { captureCount } from "./decode.mjs";
import { TakeoverCore, countUndeliveredForSession, type SyncBranchResult } from "./lib/takeover-core.mjs";
import { readdir, readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { BATCH_LIMIT, sendSessionMessages } from "./shared/batch-send.mjs";
import { claimForReplay, dequeue, incrementRetry, listPending } from "./shared/pending-queue.mjs";

/** What takeover needs from the flow's capture, as the extension runs it. */
export interface TakeoverCapture {
  /** Capture the branch through the flow (`turn_end`) and report what became of it. */
  syncBranch(branch: any[]): Promise<SyncBranchResult>;
  /** Branch entries captured through, for the published takeover checkpoint. */
  watermark(): number;
  /** Messages the server refused for good this process. */
  dropped(): number;
}

type FetchJSON = (path: string, init?: any, options?: any) => Promise<any>;

/**
 * The barrier takeover waits on before a commit: this session's queued
 * messages, drained through the batch endpoint, `BATCH_LIMIT` per request.
 * The flow replays the queue only at session start, one request per entry,
 * which let a large offline backlog block the barrier for many turns (#4504).
 * The generic replay also drops rejected entries without saying whose they
 * were; this drain counts them, because they are a capture gap.
 *
 * Bounded by `OPENVIKING_PENDING_DRAIN_BUDGET_MS` (10 s by default), narrowed
 * to what takeover's handler budget leaves, and optionally by
 * `OPENVIKING_PENDING_DRAIN_MAX_BATCHES`; the remainder drains on later turns.
 */
export function createSessionDrain(opts: { fetchJSON: FetchJSON; sessionId: () => string | null; log?: (stage: string, data: unknown) => void }) {
  let dropped = 0;
  const log = opts.log ?? (() => {});

  async function drain(sid: string, budgetMs?: number): Promise<void> {
    const budgetRaw = Number(process.env.OPENVIKING_PENDING_DRAIN_BUDGET_MS);
    const configured = Number.isFinite(budgetRaw) && budgetRaw >= 0 ? budgetRaw : 10_000;
    const timeBudgetMs = Number.isFinite(budgetMs) ? Math.max(0, Math.min(configured, Number(budgetMs))) : configured;
    const maxRaw = Number(process.env.OPENVIKING_PENDING_DRAIN_MAX_BATCHES);
    const maxBatches = Number.isFinite(maxRaw) && maxRaw > 0 ? Math.floor(maxRaw) : Number.POSITIVE_INFINITY;
    const started = Date.now();
    let batches = 0;

    const backlog = (await listPending()).filter(
      ({ entry }: any) => entry?.type === "addMessage" && entry.sessionId === sid,
    );
    for (let start = 0; start < backlog.length; start += BATCH_LIMIT) {
      if (batches >= maxBatches || Date.now() - started >= timeBudgetMs) {
        log("drain", {
          session: sid,
          stopped: batches >= maxBatches ? "max-batches" : "time-budget",
          batches,
          remaining: backlog.length - start,
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
      const result = await sendSessionMessages(opts.fetchJSON, sid, claimed.map(({ entry }) => entry.payload), {
        onSent: async (count: number) => {
          for (let i = 0; i < count; i++) await dequeue(claimed[delivered++].filename);
        },
      });
      if (delivered === claimed.length) continue;

      // A permanent rejection is removed at once; a retryable one stays queued
      // with one more retry. incrementRetry returning false (retries exhausted
      // or the queue write failed) is a permanent gap too.
      for (const { filename, entry } of claimed.slice(delivered)) {
        if (!result.retryable) {
          await dequeue(filename);
          dropped += 1;
        } else if ((await incrementRetry(filename, entry)) === false) {
          dropped += 1;
        }
      }
      log("drain", { session: sid, delivered, retried: claimed.length - delivered });
      return;
    }
  }

  return {
    /** Drain within `budgetMs`; true once nothing of this session is undelivered. */
    async flush(budgetMs?: number): Promise<boolean> {
      const sid = opts.sessionId();
      if (!sid) return false;
      await drain(sid, budgetMs);
      if (countUndeliveredForSession(await listPending(), sid) !== 0) return false;
      // A fresh `.processing` file may belong to another pi process, so its
      // absence from listPending is no proof of delivery.
      return !(await hasProcessingMessage(sid));
    },
    dropped: () => dropped,
  };
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
      // Unattributable; the regular stale recovery handles it later.
    }
  }
  return false;
}

export function createTakeoverManager(opts: {
  pi: any;
  client: OVClient;
  capture: TakeoverCapture;
  sessionId: () => string | null;
  config: OVConfig;
  log?: (message: string) => void;
}): TakeoverCore {
  const { pi, client, capture, config, sessionId } = opts;
  let lastCommitFailure = "";
  const backlog = createSessionDrain({
    fetchJSON: (path, init, options) => client.fetchJSON(path, init, options),
    sessionId,
    log: (stage, data) => opts.log?.(`${stage} ${JSON.stringify(data)}`),
  });
  return new TakeoverCore({
    config,
    io: {
      syncBranch: (branch: any[]) => capture.syncBranch(branch),
      flush: (budgetMs?: number) => backlog.flush(budgetMs),
      // Takeover's own commits: a non-zero keep_recent_count and Working
      // Memory, which the flow's commit (`keep_recent_count: 0`) does not ask for.
      commit: async (commitOpts: { keepRecentCount?: number; timeoutMs?: number; enableWorkingMemory?: boolean } = {}) => {
        const sid = sessionId();
        if (!sid) {
          lastCommitFailure = "no OpenViking session yet";
          return null;
        }
        const response = await client.commitSessionResponse(
          sid, commitOpts.keepRecentCount, commitOpts.timeoutMs, commitOpts.enableWorkingMemory,
        );
        lastCommitFailure = response.result ? "" : describeCommitError(response.status, response.error);
        opts.log?.(`commit ${response.result ? "ok" : `failed: ${lastCommitFailure}`} (trace_id=${response.traceId || "none"})`);
        return response.result;
      },
      // Read the Working Memory of the exact archive this commit produced, not
      // the session's newest `/context` overview — the latter can be an older
      // archive this takeover did not create.
      readArchiveOverview: (archiveUri: string, timeoutMs?: number) => client.readArchiveOverview(archiveUri, timeoutMs),
      archiveState: (archiveUri: string, timeoutMs?: number) => client.getArchiveState(archiveUri, timeoutMs),
      captureCount: (branchSlice: any[]) => captureCount(branchSlice, config),
      persistEntry: (customType: string, data: any) => {
        if (typeof pi?.appendEntry === "function") pi.appendEntry(customType, data);
      },
      getWatermark: () => capture.watermark(),
      droppedCount: () => capture.dropped() + backlog.dropped(),
      lastCommitError: () => lastCommitFailure,
      availableTools: () => typeof pi?.getActiveTools === "function" ? pi.getActiveTools() : [],
      log: opts.log,
    },
  });
}

/** One line for a failed commit request: status (0 = no response) and the server's message. */
export function describeCommitError(status: number | undefined, error: any): string {
  const code = Number(status) || 0;
  const raw = String(error?.message || error?.code || "unknown error").replace(/\s+/g, " ").trim();
  const message = raw.length > 200 ? `${raw.slice(0, 200)}…` : raw;
  return code > 0 ? `HTTP ${code}: ${message}` : `no response: ${message}`;
}
