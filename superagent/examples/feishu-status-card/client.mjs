// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The two calls a status card needs: send it once, then patch the same message.
//
// Deliberately small: the tenant token, one send and one patch, no SDK. The
// card JSON is the only format that matters here, and an SDK would add a model
// to translate to and from without covering the patch call any better.
//
// Failure handling is the part worth getting right. A status card is the only
// place a reader sees a task change, so a dropped patch leaves the card
// claiming an old state until the next change. Retries are limited to what
// cannot duplicate anything: a patch is idempotent, a send is not, so a send is
// retried only when the platform says it did not accept it (429), never on a
// 5xx that may have delivered the card already.

import { MAX_CARD_BYTES } from './card.mjs';

const DEFAULT_BASE = 'https://open.feishu.cn';
const RETRYABLE = new Set([429, 500, 502, 503, 504]);
// Feishu answers some rate limits with HTTP 400 and this code.
const RATE_LIMIT_CODES = new Set([429, 99991400]);
const BACKOFF_MS = [300, 900];

export class FeishuError extends Error {
  constructor(message, { path, code = null, status = null } = {}) {
    super(message);
    this.name = 'FeishuError';
    this.path = path;
    this.code = code;
    this.status = status;
  }
}

function assertCard(card, what) {
  if (!card || typeof card !== 'object' || !Array.isArray(card.elements)) {
    throw new FeishuError(`${what}: card must be an interactive card object with elements`);
  }
  const bytes = Buffer.byteLength(JSON.stringify(card));
  if (bytes > MAX_CARD_BYTES) throw new FeishuError(`${what}: card is ${bytes} bytes, over the ${MAX_CARD_BYTES} limit`);
}

export function createFeishuClient({
  appId,
  appSecret,
  baseUrl = DEFAULT_BASE,
  fetchImpl = globalThis.fetch,
  dryRun = false,
  now = Date.now,
  sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); }),
} = {}) {
  if (!dryRun && (!appId || !appSecret)) throw new Error('appId and appSecret are required unless dryRun');
  if (typeof fetchImpl !== 'function') throw new Error('fetch is unavailable; pass fetchImpl');

  let token = null;
  const calls = [];

  async function request(path, { method = 'POST', body, headers = {}, query = '' } = {}) {
    const url = `${baseUrl}${path}${query}`;
    const response = await fetchImpl(url, {
      method,
      headers: { 'content-type': 'application/json; charset=utf-8', ...headers },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    let parsed;
    try { parsed = text ? JSON.parse(text) : {}; } catch {
      throw new FeishuError(`feishu ${path}: non-JSON response (${response.status})`, { path, status: response.status });
    }
    if (!response.ok || (parsed.code !== undefined && parsed.code !== 0)) {
      throw new FeishuError(`feishu ${path}: code=${parsed.code ?? response.status} msg=${parsed.msg ?? response.statusText}`,
        { path, code: parsed.code ?? null, status: response.status });
    }
    return parsed;
  }

  async function tenantToken() {
    if (dryRun) return 'dry-run-token';
    if (token && token.expiresAt > now() + 60_000) return token.value;
    const data = await request('/open-apis/auth/v3/tenant_access_token/internal', {
      body: { app_id: appId, app_secret: appSecret },
    });
    if (!data.tenant_access_token) throw new FeishuError('feishu: tenant token response had no token', { path: '/open-apis/auth/v3/tenant_access_token/internal' });
    token = { value: data.tenant_access_token, expiresAt: now() + (data.expire ?? 7200) * 1000 };
    return token.value;
  }

  // `idempotent` decides whether a 5xx is retried: patching the same card twice
  // ends in one card, sending it twice ends in two.
  async function call(name, path, { idempotent, ...options }) {
    if (dryRun) {
      calls.push({ name, path, options });
      return { code: 0, dryRun: true, data: { message_id: `om_dryrun_${calls.length}` } };
    }
    const auth = { authorization: `Bearer ${await tenantToken()}` };
    for (let attempt = 0; ; attempt += 1) {
      try {
        const data = await request(path, { ...options, headers: auth });
        calls.push({ name, path, options });
        return data;
      } catch (error) {
        const rateLimited = error instanceof FeishuError
          && (error.status === 429 || RATE_LIMIT_CODES.has(error.code));
        const retryable = error instanceof FeishuError
          && (rateLimited || (idempotent && RETRYABLE.has(error.status)))
          && attempt < BACKOFF_MS.length;
        if (!retryable) throw error;
        await sleep(BACKOFF_MS[attempt]);
      }
    }
  }

  return {
    calls,
    tenantToken,
    // Reply in the thread the task came from when there is one; otherwise post
    // to the chat. Both return the message_id the patch below needs.
    async sendCard({ chatId, card, replyTo = null }) {
      assertCard(card, 'send');
      return replyTo
        ? call('reply', `/open-apis/im/v1/messages/${encodeURIComponent(replyTo)}/reply`, {
          idempotent: false,
          body: { msg_type: 'interactive', content: JSON.stringify(card) },
        })
        : call('send', '/open-apis/im/v1/messages', {
          idempotent: false,
          query: '?receive_id_type=chat_id',
          body: { receive_id: chatId, msg_type: 'interactive', content: JSON.stringify(card) },
        });
    },
    // Replaces the whole card. `update_multi` in the card config is what makes
    // Feishu apply the new rendering instead of keeping the first one.
    async patchCard({ messageId, card }) {
      assertCard(card, 'patch');
      return call('patch', `/open-apis/im/v1/messages/${encodeURIComponent(messageId)}`, {
        idempotent: true,
        method: 'PATCH',
        body: { content: JSON.stringify(card) },
      });
    },
  };
}
