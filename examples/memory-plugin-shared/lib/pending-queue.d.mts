export function enqueue(type: string, sessionId: string, payload: Record<string, any>): Promise<{ ok: boolean; path?: string; error?: string }>;
export function listPending(): Promise<Array<{ filename: string; entry: Record<string, any> }>>;

export type PendingReplayStatus = "sent" | "failed" | "skipped" | "deferred";

export interface PendingReplayResult {
  type: string;
  sessionId: string;
  dedupKey: string;
  status: PendingReplayStatus;
  errorKind?: string;
  statusCode?: number;
  retries?: number;
  dequeueFailed?: boolean;
}

export interface PendingReplaySummary {
  replayed: number;
  failed: number;
  skipped: number;
  deferred: number;
}

export function replayPending(
  fetchJSON: (path: string, init?: any) => Promise<{ ok: boolean; status?: number; result?: any; error?: any }>,
  log: (stage: string, data?: any) => void,
  options?: { consumeRetries?: boolean; shouldReplay?: (entry: Record<string, any>) => boolean | Promise<boolean> },
): Promise<PendingReplaySummary>;

export function replayPendingDetailed(
  fetchJSON: (path: string, init?: any) => Promise<{ ok: boolean; status?: number; result?: any; error?: any }>,
  log: (stage: string, data?: any) => void,
  options?: { consumeRetries?: boolean; shouldReplay?: (entry: Record<string, any>) => boolean | Promise<boolean> },
): Promise<PendingReplaySummary & {
  results: PendingReplayResult[];
  cleaned: number;
  lost: Array<Record<string, any>>;
}>;

export function claimForReplay(filename: string): Promise<string | null>;
export function releaseClaim(claimedFilename: string): Promise<string | null>;
export function dequeue(filename: string): Promise<boolean>;
export function incrementRetry(filename: string, entry: Record<string, any>): Promise<boolean>;

/** The three outcomes a retry can have; only `dropped` is a loss. */
export type RetryOutcome = "kept" | "dropped" | "failed";

export function incrementRetryDetailed(
  filename: string,
  entry: Record<string, any>,
): Promise<{ outcome: RetryOutcome; retries: number; filename?: string; error?: string }>;
export function cleanStale(): Promise<number>;
export function listPendingReadOnly(): Promise<Array<{ filename: string; entry: Record<string, any> }>>;

export function cleanStaleDetailed(options?: { now?: number; exclude?: Set<string> | null }): Promise<{
  cleaned: number;
  lost: Array<{ filename: string; entry: Record<string, any> }>;
}>;
