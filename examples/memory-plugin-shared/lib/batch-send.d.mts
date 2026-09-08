export const BATCH_LIMIT: number;
export function sendSessionMessages(
  fetchJSON: (path: string, init?: any) => Promise<{ ok: boolean; status?: number; result?: any; error?: any }>,
  sessionId: string,
  payloads: Array<Record<string, any>>,
  opts?: { enqueueOnRetryable?: boolean; onSent?: (count: number) => void | Promise<void> },
): Promise<{
  sent: number;
  queued: number;
  enqueueFailed: number;
  failed: number;
  retryable: boolean;
  usedBatch: boolean;
  lastError: any;
}>;
