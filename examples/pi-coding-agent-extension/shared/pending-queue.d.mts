export interface QueueScope {
  readonly producer: string;
  readonly targetHash: string;
  readonly dir: string;
}
export function createQueueScope(options: {
  producer: string;
  baseUrl: string;
  account?: string;
  user?: string;
  apiKey?: string;
}): Promise<QueueScope>;
export function enqueue(scope: QueueScope, type: string, sessionId: string, payload: Record<string, any>, options?: { provenance?: string }): Promise<{ ok: boolean; path?: string; error?: string; deduped?: boolean; dedupKey?: string }>;
export function listPending(scope: QueueScope): Promise<Array<{ filename: string; entry: Record<string, any> }>>;
export function replayPending(
  scope: QueueScope,
  fetchJSON: (path: string, init?: any) => Promise<{ ok: boolean; status?: number; result?: any; error?: any }>,
  log: (stage: string, data?: any) => void,
): Promise<{ replayed: number; failed: number; skipped: number; deferred: number }>;
export function cleanStale(scope: QueueScope): Promise<number>;
