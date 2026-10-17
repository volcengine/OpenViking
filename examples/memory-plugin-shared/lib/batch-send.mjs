import { enqueue } from "./pending-queue.mjs";
import { isRetryableFailure } from "./retryable.mjs";

export const BATCH_LIMIT = 100;

// Serial isolation stops after this many rejections in a row: a batch the
// server refuses wholesale must not turn into a hundred doomed requests.
const MAX_CONSECUTIVE_REJECTIONS = 3;

export function isRetryableSendFailure(res) {
  return isRetryableFailure(res);
}

function makeResult() {
  return {
    sent: 0,
    queued: 0,
    enqueueFailed: 0,
    failed: 0,
    retryable: false,
    usedBatch: true,
    lastError: null,
  };
}

async function enqueueRemainder(sessionId, payloads, startIndex, result, retryable, lastError) {
  result.retryable = retryable;
  result.lastError = lastError ?? null;

  if (!retryable) {
    result.failed += Math.max(0, payloads.length - startIndex);
    return result;
  }

  const baseCreatedAt = Date.now();
  for (let i = startIndex; i < payloads.length; i++) {
    const queued = await enqueue("addMessage", sessionId, payloads[i], {
      createdAt: baseCreatedAt + (i - startIndex),
    });
    if (!queued.ok) {
      // Stop at the first enqueue failure so queued entries stay a contiguous
      // prefix: consumers mark the first sent+queued payloads as captured, so
      // skipping a payload here and queueing a later one would silently drop it.
      result.enqueueFailed += payloads.length - i;
      return result;
    }
    result.queued++;
  }
  return result;
}

async function sendSerial(fetchJSON, sessionId, payloads, startIndex, opts, result) {
  result.usedBatch = false;
  const encodedSid = encodeURIComponent(sessionId);
  let consecutiveRejects = 0;
  for (let i = startIndex; i < payloads.length; i++) {
    const res = await fetchJSON(`/api/v1/sessions/${encodedSid}/messages`, {
      method: "POST",
      body: JSON.stringify(payloads[i]),
    });
    if (res?.ok) {
      result.sent++;
      consecutiveRejects = 0;
      await opts.onConfirmed?.([payloads[i]]);
      await opts.onSent?.(1);
      continue;
    }

    const retryable = isRetryableSendFailure(res);
    if (retryable) {
      if (opts.enqueueOnRetryable) {
        return enqueueRemainder(sessionId, payloads, i, result, retryable, res?.error ?? res);
      }
      result.retryable = true;
      result.lastError = res?.error ?? res ?? null;
      result.failed += payloads.length - i;
      return result;
    }

    // One payload the server refuses outright must not block its neighbours:
    // count it, hand it to the caller, and keep going. The commit gate treats
    // result.failed as stranded, so nothing archives over the rejection.
    result.failed++;
    consecutiveRejects += 1;
    result.lastError = res?.error ?? res ?? null;
    await opts.onRejected?.(payloads[i], res);
    if (consecutiveRejects >= MAX_CONSECUTIVE_REJECTIONS) {
      // The batch looks rejected as a whole (schema, size), not one bad entry:
      // stop spending requests and count the rest as failed.
      result.failed += payloads.length - i - 1;
      return result;
    }
  }
  return result;
}

/**
 * Send add-message payloads in server-sized batches with serial fallback.
 *
 * @param {Function} fetchJSON - (path, init) => { ok, status, result?, error? }
 * @param {string} sessionId - OpenViking session id
 * @param {Array<object>} payloads - sanitized add-message request bodies
 * @param {object} opts
 * @param {boolean} opts.enqueueOnRetryable - enqueue unsent payloads after a retryable failure
 * @param {Function} opts.onSent - called with the number of messages durably sent after each success
 * @param {Function} opts.onConfirmed - called with an array of the payloads the server durably accepted (batch: the chunk, serial: one element)
 * @param {Function} opts.onRejected - called with a payload the server refused outright, which does not stop the run
 * @returns {Promise<{sent:number,queued:number,enqueueFailed:number,failed:number,retryable:boolean,usedBatch:boolean,lastError:any}>}
 */
export async function sendSessionMessages(fetchJSON, sessionId, payloads, opts = {}) {
  const result = makeResult();
  const messages = Array.isArray(payloads) ? payloads : [];
  if (messages.length === 0) return result;

  const encodedSid = encodeURIComponent(sessionId);
  for (let start = 0; start < messages.length; start += BATCH_LIMIT) {
    const chunk = messages.slice(start, start + BATCH_LIMIT);
    const res = await fetchJSON(`/api/v1/sessions/${encodedSid}/messages/batch`, {
      method: "POST",
      body: JSON.stringify({ messages: chunk }),
    });

    if (res?.ok) {
      result.sent += chunk.length;
      await opts.onConfirmed?.(chunk);
      await opts.onSent?.(chunk.length);
      continue;
    }

    const status = Number(res?.status || 0);
    const retryable = isRetryableSendFailure(res);
    // 404/405 mean the batch route is absent; any other refusal is the batch
    // being rejected, and serial is how one bad payload is isolated from its
    // neighbours instead of poisoning the whole remainder.
    if (status === 404 || status === 405 || !retryable) {
      return sendSerial(fetchJSON, sessionId, messages, start, opts, result);
    }
    if (opts.enqueueOnRetryable) {
      return enqueueRemainder(sessionId, messages, start, result, retryable, res?.error ?? res);
    }
    result.retryable = retryable;
    result.lastError = res?.error ?? res ?? null;
    result.failed += messages.length - start;
    return result;
  }

  return result;
}
