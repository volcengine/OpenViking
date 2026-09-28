export function enqueue(type: string, sessionId: string, payload: Record<string, any>): Promise<{ ok: boolean; path?: string; error?: string }>;
export function listPending(): Promise<Array<{ filename: string; entry: Record<string, any> }>>;
export function replayPending(
  fetchJSON: (path: string, init?: any) => Promise<{ ok: boolean; status?: number; result?: any; error?: any }>,
  log: (stage: string, data?: any) => void,
): Promise<{ replayed: number; failed: number; skipped: number; deferred: number }>;
export function claimForReplay(filename: string): Promise<string | null>;
export function dequeue(filename: string): Promise<boolean>;
export function incrementRetry(filename: string, entry: Record<string, any>): Promise<boolean>;
export function cleanStale(): Promise<number>;
