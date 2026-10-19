export type PendingReplayOutcome =
  | "sent"
  | "rejected"
  | "failed"
  | "deferred"
  | "dropped-exhausted"
  | "dropped-unknown-type"
  | "skipped-claimed";

export interface PendingReplayResult {
  filename: string;
  type: string;
  sessionId: string;
  outcome: PendingReplayOutcome;
  status?: number;
  retries?: number;
  reason?: "limit" | "ordering";
  payload?: Record<string, any>;
}

export function enqueue(type: string, sessionId: string, payload: Record<string, any>): Promise<{ ok: boolean; path?: string; error?: string }>;
export function listPending(options?: { includeProcessing?: boolean }): Promise<Array<{ filename: string; entry: Record<string, any>; processing?: true }>>;
export function replayPending(
  fetchJSON: (path: string, init?: any) => Promise<{ ok: boolean; status?: number; result?: any; error?: any }>,
  log: (stage: string, data?: any) => void,
  options?: { consumeRetries?: boolean; includePayload?: boolean },
): Promise<{
  replayed: number;
  failed: number;
  skipped: number;
  deferred: number;
  cleaned: number;
  results: PendingReplayResult[];
}>;
export function claimForReplay(filename: string): Promise<string | null>;
export function dequeue(filename: string): Promise<boolean>;
export function incrementRetry(filename: string, entry: Record<string, any>): Promise<boolean>;
export function cleanStale(): Promise<number>;
