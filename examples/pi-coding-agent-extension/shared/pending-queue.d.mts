export function enqueue(type: string, sessionId: string, payload: Record<string, any>): Promise<{ ok: boolean; path?: string; error?: string }>;
export function listPending(): Promise<Array<{ filename: string; entry: Record<string, any> }>>;
export function drainPendingForSession(
  fetchJSON: (path: string, init?: any) => Promise<{ ok: boolean; status?: number; result?: any; error?: any }>,
  log: (stage: string, data?: any) => void,
  sessionId: string,
  options?: { maxItems?: number; timeBudgetMs?: number },
): Promise<{ replayed: number; failed: number; skipped: number; deferred: number; remaining: number }>;
export function replayPending(
  fetchJSON: (path: string, init?: any) => Promise<{ ok: boolean; status?: number; result?: any; error?: any }>,
  log: (stage: string, data?: any) => void,
): Promise<{ replayed: number; failed: number; skipped: number; deferred: number }>;
export function cleanStale(): Promise<number>;
