export function enqueue(
  type: string,
  sessionId: string,
  payload: Record<string, any>,
  options?: { createdAt?: number; origin?: string },
): Promise<{ ok: boolean; path?: string; error?: string }>;
export function matchesOrigin(entry: Record<string, any>, origin?: string): boolean;
export function listPending(): Promise<Array<{ filename: string; entry: Record<string, any> }>>;
export function replayPending(
  fetchJSON: (path: string, init?: any) => Promise<{ ok: boolean; status?: number; result?: any; error?: any }>,
  log: (stage: string, data?: any) => void,
  options?: { consumeRetries?: boolean },
): Promise<{ replayed: number; failed: number; skipped: number; deferred: number; authFailed?: boolean; lastError?: any }>;
export function claimForReplay(filename: string): Promise<string | null>;
export function dequeue(filename: string): Promise<boolean>;
export function incrementRetry(filename: string, entry: Record<string, any>): Promise<boolean>;
export function cleanStale(): Promise<number>;
export function releaseClaim(claimedFilename: string): Promise<string | null>;
export function hasPendingWrites(sessionId: string, origin?: string): Promise<boolean>;
export function enqueueCommitAfterWrites(
  sessionId: string,
  payload?: Record<string, any>,
  options?: { origin?: string },
): Promise<{ ok: boolean; path?: string; error?: string }>;
export function deferCommitBehindPendingWrites(
  fetchJSON: (path: string, init?: any) => Promise<{ ok: boolean; status?: number; result?: any; error?: any }>,
  sessionId: string,
  payload?: Record<string, any>,
  log?: (stage: string, data?: any) => void,
): Promise<{ ok: boolean; path?: string; error?: string } | null>;
export function commitSession(
  fetchJSON: (path: string, init?: any) => Promise<{ ok: boolean; status?: number; result?: any; error?: any }>,
  sessionId: string,
  body?: Record<string, any>,
  options?: { queue?: boolean; log?: (stage: string, data?: any) => void },
): Promise<{
  ok: boolean;
  status?: number;
  result?: any;
  error?: any;
  traceId?: string;
  deferred?: boolean;
  pendingQueued?: boolean;
  pendingEnqueueFailed?: boolean;
}>;
