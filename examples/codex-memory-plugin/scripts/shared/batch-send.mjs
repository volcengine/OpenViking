// GENERATED FROM examples/memory-plugin-shared/lib. DO NOT EDIT.
import { enqueue, hasPendingWrites, listPending, replayPending } from "./pending-queue.mjs";
import { isAuthFailure, isRetryableFailure } from "./retryable.mjs";

export const BATCH_LIMIT = 100;

export function isRetryableSendFailure(res) {
  return isRetryableFailure(res);
}

function makeResult() {
  return {
    sent: 0,
    queued: 0,
    enqueueFailed: 0,
    failed: 0,
    retryLater: 0,
    retryable: false,
    authFailed: false,
    usedBatch: true,
    lastError: null,
  };
}

async function latestQueuedAt(sessionId, origin) {
  let latest = 0;
  for (const { entry } of await listPending()) {
    if (entry.sessionId !== sessionId || (origin && entry.origin && entry.origin !== origin)) continue;
    latest = Math.max(latest, Number(entry.createdAt) || 0);
  }
  return latest;
}

async function enqueueRemainder(sessionId, payloads, startIndex, result, opts, { after = 0 } = {}) {
  const baseCreatedAt = Math.max(Date.now(), after + 1);
  for (let i = startIndex; i < payloads.length; i++) {
    const queued = await enqueue("addMessage", sessionId, payloads[i], {
      createdAt: baseCreatedAt + (i - startIndex),
      origin: opts.origin,
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

/** Everything from `index` on is unsent and still the caller's to deliver. */
function holdRemainder(payloads, index, result, { retryable, authFailed = false, error }) {
  const remaining = payloads.length - index;
  result.failed += remaining;
  result.retryLater += remaining;
  result.retryable = retryable;
  result.authFailed = authFailed;
  result.lastError = error ?? null;
  return result;
}

async function stopAt(sessionId, payloads, index, res, opts, result) {
  const error = res?.error ?? res;
  if (isAuthFailure(res)) return holdRemainder(payloads, index, result, { retryable: false, authFailed: true, error });
  if (isRetryableSendFailure(res)) {
    if (!opts.enqueueOnRetryable) return holdRemainder(payloads, index, result, { retryable: true, error });
    result.retryable = true;
    result.lastError = error ?? null;
    return enqueueRemainder(sessionId, payloads, index, result, opts);
  }
  return null;
}

async function reject(count, res, opts, result) {
  result.failed += count;
  result.lastError = res?.error ?? res ?? null;
  await opts.onRejected?.(count);
}

// One message per request, `start` up to `end`. Returns the result once the
// remainder of the whole send is settled, or null to carry on batching.
async function sendSerial(fetchJSON, sessionId, payloads, start, end, opts, result) {
  const encodedSid = encodeURIComponent(sessionId);
  for (let i = start; i < end; i++) {
    const res = await fetchJSON(`/api/v1/sessions/${encodedSid}/messages`, {
      method: "POST",
      body: JSON.stringify(payloads[i]),
    });
    if (res?.ok) {
      result.sent++;
      await opts.onSent?.(1);
      continue;
    }
    const stopped = await stopAt(sessionId, payloads, i, res, opts, result);
    if (stopped) return stopped;
    // The server creates a session on its first message, so a 404 here says
    // nothing about the content: keep the rest for the caller's next attempt.
    if (Number(res?.status) === 404) {
      return holdRemainder(payloads, i, result, { retryable: false, error: res?.error ?? res });
    }
    if (opts.isolateRejected === false) {
      await reject(payloads.length - i, res, opts, result);
      return result;
    }
    await reject(1, res, opts, result);
  }
  return null;
}

/**
 * Send add-message payloads in server-sized batches.
 *
 * Every harness handles a failed send the same way, so this function owns the
 * decision and the caller only moves its cursor by the result:
 *
 * - The session's queued messages go first. When it has some, the queue is
 *   replayed before sending; if any are still queued after that, the new
 *   messages are queued behind them (or, without `enqueueOnRetryable`, held
 *   back as `retryLater`) so the server stores them in order.
 * - 401 / 403: nothing more is sent or queued. The unsent messages are
 *   `retryLater` with `authFailed` set, to be sent again once the credentials
 *   work.
 * - A retryable failure (network, 408, 429, 5xx): the unsent messages are
 *   queued with `enqueueOnRetryable`, otherwise they are `retryLater`.
 * - Any other refusal of a batch (413 too large, 422 one bad message, ...): the
 *   batch is sent again one message per request, so only the messages the
 *   server refuses count as `failed`, and the rest land. A refused message is
 *   never sent again: the caller advances past it.
 * - A server without the batch route (404 / 405 on it) gets every message one
 *   by one. A 404 on the one-message route leaves the unsent messages
 *   `retryLater`.
 *
 * Payloads are settled in order. All but the last `retryLater + enqueueFailed`
 * are settled (sent, queued, or refused for good); the caller keeps the rest
 * for its next attempt. `failed` counts the refused messages and the
 * `retryLater` ones; `enqueueFailed` ones are counted only there.
 *
 * Queued entries record the server and identity they were queued for, read
 * from `fetchJSON.origin` (set by `createOvHttp`).
 *
 * @param {Function} fetchJSON - (path, init) => { ok, status, result?, error? }
 * @param {string} sessionId - OpenViking session id
 * @param {Array<object>} payloads - sanitized add-message request bodies
 * @param {object} opts
 * @param {boolean} opts.enqueueOnRetryable - enqueue unsent payloads after a retryable failure
 * @param {Function} opts.onSent - called with the number of messages durably sent after each success
 * @param {Function} opts.onRejected - called with the number of messages refused for good, in order with onSent
 * @param {boolean} [opts.replayFirst=true] - false for a caller that is itself sending queued entries
 * @param {boolean} [opts.isolateRejected=true] - false fails everything from the first refused message on
 * @param {Function} [opts.log] - pending-queue logger for the replay
 * @returns {Promise<{sent:number,queued:number,enqueueFailed:number,failed:number,retryLater:number,retryable:boolean,authFailed:boolean,usedBatch:boolean,lastError:any}>}
 */
export async function sendSessionMessages(fetchJSON, sessionId, payloads, opts = {}) {
  const result = makeResult();
  const messages = Array.isArray(payloads) ? payloads : [];
  if (messages.length === 0) return result;
  const origin = fetchJSON?.origin;
  const options = { ...opts, origin };

  if (opts.replayFirst !== false && await hasPendingWrites(sessionId, origin)) {
    let replay = null;
    try {
      replay = await replayPending(fetchJSON, opts.log || (() => {}), { consumeRetries: false });
    } catch {
      // Whatever is left queued is handled below.
    }
    if (replay?.authFailed) {
      return holdRemainder(messages, 0, result, { retryable: false, authFailed: true, error: replay.lastError });
    }
    if (await hasPendingWrites(sessionId, origin)) {
      const error = { code: "PENDING_WRITES", message: "queued messages for this session have not been replayed yet" };
      if (!opts.enqueueOnRetryable) return holdRemainder(messages, 0, result, { retryable: true, error });
      result.retryable = true;
      result.lastError = error;
      return enqueueRemainder(sessionId, messages, 0, result, options, {
        after: await latestQueuedAt(sessionId, origin),
      });
    }
  }

  const encodedSid = encodeURIComponent(sessionId);
  for (let start = 0; start < messages.length; start += BATCH_LIMIT) {
    const end = Math.min(messages.length, start + BATCH_LIMIT);
    const chunk = messages.slice(start, end);
    const res = await fetchJSON(`/api/v1/sessions/${encodedSid}/messages/batch`, {
      method: "POST",
      body: JSON.stringify({ messages: chunk }),
    });

    if (res?.ok) {
      result.sent += chunk.length;
      await opts.onSent?.(chunk.length);
      continue;
    }

    const status = Number(res?.status || 0);
    if (status === 404 || status === 405) {
      result.usedBatch = false;
      return (await sendSerial(fetchJSON, sessionId, messages, start, messages.length, options, result)) || result;
    }

    const stopped = await stopAt(sessionId, messages, start, res, options, result);
    if (stopped) return stopped;

    if (opts.isolateRejected === false) {
      await reject(messages.length - start, res, options, result);
      return result;
    }
    if (chunk.length === 1) {
      await reject(1, res, options, result);
      continue;
    }
    const settled = await sendSerial(fetchJSON, sessionId, messages, start, end, options, result);
    if (settled) return settled;
  }

  return result;
}
