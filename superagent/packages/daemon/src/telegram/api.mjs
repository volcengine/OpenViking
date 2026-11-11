// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { setTimeout as delay } from 'node:timers/promises';

// https://core.telegram.org/bots/api — use the hosted Bot API, never a user session.
export const API_BASE = 'https://api.telegram.org';
export const TELEGRAM_VERSION = 'telegram-bot-api/v1';
export const TEXT_LIMIT = 4096;
export const DOWNLOAD_BYTES = 20_000_000;
export const PHOTO_BYTES = 10_000_000;
export const DOCUMENT_BYTES = 50_000_000;

export function integer(value, label = 'identifier', minimum = 0) {
  if (!Number.isSafeInteger(value) || value < minimum) throw new Error(`Invalid Telegram ${label}`);
  return value;
}

export class TelegramError extends Error {
  constructor(status, code, retryAfter = null) {
    super(`Telegram API HTTP ${status} (code ${Number.isSafeInteger(code) ? code : 'unknown'})`);
    this.status = status;
    this.code = Number.isSafeInteger(code) ? code : status;
    this.retryAfter = Number.isSafeInteger(retryAfter) && retryAfter >= 0 ? retryAfter : null;
  }
}

export async function readBounded(response, maxBytes) {
  const reader = response.body?.getReader();
  if (!reader) throw new Error('Telegram response has no body');
  const parts = [];
  let length = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > maxBytes) throw new Error('Telegram response exceeds the size limit');
      parts.push(Buffer.from(value));
    }
    return Buffer.concat(parts);
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

export class TelegramApi {
  constructor(token, { fetchImpl = globalThis.fetch } = {}) {
    if (typeof token !== 'string' || !/^\d{1,20}:[A-Za-z0-9_-]{20,200}$/.test(token)) {
      throw new Error('Invalid Telegram bot token');
    }
    this.token = token;
    this.fetchImpl = fetchImpl;
  }

  async request(method, body = {}, signal = AbortSignal.timeout(30_000)) {
    if (!['getMe', 'getWebhookInfo', 'getUpdates', 'sendMessage', 'sendPhoto', 'sendDocument', 'getFile'].includes(method)) {
      throw new Error('Unsupported Telegram API method');
    }
    try {
      signal.throwIfAborted();
      const response = await this.fetchImpl(`${API_BASE}/bot${this.token}/${method}`, {
        method: 'POST', redirect: 'error', signal,
        ...(body instanceof FormData ? { body } : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
      });
      let result;
      try { result = JSON.parse((await readBounded(response, 4 * 1024 * 1024)).toString('utf8')); }
      catch { throw new Error(`Telegram API returned an invalid response (HTTP ${response.status})`); }
      if (!response.ok || result?.ok !== true) throw new TelegramError(response.status, result?.error_code, result?.parameters?.retry_after);
      if (!Object.hasOwn(result, 'result')) throw new Error('Telegram API returned no result');
      return result.result;
    } catch (error) {
      // Tokens are in the URL. Never propagate fetch errors, causes, provider
      // descriptions, or aborted request URLs into Core logs or tool output.
      if (signal.aborted) throw new Error('Telegram request cancelled or timed out');
      if (error instanceof TelegramError) throw error;
      throw new Error('Telegram request failed or returned invalid data');
    }
  }

  async inspect(signal) {
    const me = await this.request('getMe', {}, signal);
    integer(me?.id, 'bot id', 1);
    if (me.is_bot !== true || !/^[A-Za-z0-9_]{5,32}$/.test(me.username ?? '')) throw new Error('Invalid Telegram bot identity');
    const webhook = await this.request('getWebhookInfo', {}, signal);
    if (typeof webhook?.url !== 'string') throw new Error('Invalid Telegram webhook status');
    if (webhook.url) throw new Error('Telegram bot has an active webhook; use a dedicated bot or remove its webhook first');
    return { botId: String(me.id), username: me.username };
  }

  async updates(offset, signal) {
    const batch = await this.request('getUpdates', { offset: integer(offset, 'offset'), timeout: 25,
      limit: 100, allowed_updates: ['message'] }, signal);
    if (!Array.isArray(batch) || batch.length > 100) throw new Error('Invalid Telegram update batch');
    let previous = -1;
    for (const update of batch) {
      const id = integer(update?.update_id, 'update id');
      if (id <= previous) throw new Error('Invalid Telegram update ordering');
      previous = id;
    }
    return batch;
  }

  async send(method, body, signal) {
    for (let attempt = 0; ; attempt += 1) {
      try { return await this.request(method, body, signal); } catch (error) {
        // Only explicit rate limiting is safe to retry. Never retry uncertain
        // sends (network/5xx/invalid responses); Bot API has no idempotency key.
        if (attempt >= 2 || !(error instanceof TelegramError) || error.code !== 429 || error.retryAfter === null) throw error;
        await delay(Math.max(1, error.retryAfter) * 1000, undefined, { signal });
      }
    }
  }

  async download(filePath, signal) {
    if (typeof filePath !== 'string' || filePath.length > 1024 || !/^[A-Za-z0-9_./-]+$/.test(filePath)
      || filePath.split('/').some((part) => !part || part === '.' || part === '..')) throw new Error('Invalid Telegram file path');
    try {
      signal.throwIfAborted();
      const response = await this.fetchImpl(`${API_BASE}/file/bot${this.token}/${filePath}`, { redirect: 'error', signal });
      if (!response.ok) { await response.body?.cancel(); throw new Error('download rejected'); }
      const bytes = await readBounded(response, DOWNLOAD_BYTES);
      signal.throwIfAborted();
      return bytes;
    } catch { throw new Error('Telegram media download failed or exceeded the size limit'); }
  }
}
