export const BATCH_LIMIT: number;
export function isRetryableSendFailure(res: any): boolean;
export function sendSessionMessages(
  fetchJSON: ((path: string, init?: any) => Promise<{ ok: boolean; status?: number; result?: any; error?: any }>) & { origin?: string },
  sessionId: string,
  payloads: Array<Record<string, any>>,
  opts?: {
    enqueueOnRetryable?: boolean;
    onSent?: (count: number) => void | Promise<void>;
    onRejected?: (count: number) => void | Promise<void>;
    replayFirst?: boolean;
    isolateRejected?: boolean;
    log?: (stage: string, data?: any) => void;
  },
): Promise<{
  sent: number;
  queued: number;
  enqueueFailed: number;
  failed: number;
  retryLater: number;
  retryable: boolean;
  authFailed: boolean;
  usedBatch: boolean;
  lastError: any;
}>;
