// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// QQ Open Platform API v2. The gateway transport uses Tencent's MIT SDK;
// HTTP stays here so Core timeouts, cancellation and log redaction also cover uploads.
export const QQ_VERSION = 'qq-bot-api/v2';
export const API_BASE = 'https://api.bot.qq.com';
export const TEXT_LIMIT = 4000;
export const MEDIA_BYTES = 20 * 1024 * 1024;
export const REPLY_WINDOW_MS = 60 * 60 * 1000;

export function identifier(value, label = 'identifier', max = 512) {
  if (typeof value !== 'string' || !value || value.length > max || /[\s\x00-\x1f\x7f]/.test(value)) {
    throw new Error(`Invalid QQ ${label}`);
  }
  return value;
}

export function openid(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{1,256}$/.test(value)) throw new Error('Invalid QQ user OpenID');
  return value;
}

export function credentials(appId, appSecret) {
  if (typeof appId !== 'string' || !/^\d{1,32}$/.test(appId)
    || typeof appSecret !== 'string' || !/^[A-Za-z0-9_-]{16,256}$/.test(appSecret)) throw new Error('Invalid QQ AppID or AppSecret');
}

const HINTS = new Map([
  [304103, 'reply expired; ask the owner to send a new message'],
  [40034005, 'reply expired; ask the owner to send a new message'],
  [40034128, 'reply time or count limit reached; wait for a new owner message'],
  [40034100, 'proactive message quota exhausted'], [40034105, 'proactive messages are not permitted'],
  [40054004, 'the owner must add this bot as a friend'], [40054013, 'the owner has disabled messages'],
  [40054005, 'duplicate reply sequence; delivery must not be retried'], [40054016, 'bot is offline'],
  [40093002, 'daily file upload quota exhausted'],
]);

export class QQError extends Error {
  constructor(status, code) {
    const numeric = Number.isSafeInteger(Number(code)) ? Number(code) : null;
    super(`QQ API HTTP ${status} (code ${numeric ?? 'unknown'})${HINTS.has(numeric) ? `: ${HINTS.get(numeric)}` : ''}`);
    this.status = status;
    this.code = numeric;
  }
}

export async function readBounded(response, maxBytes) {
  const reader = response.body?.getReader();
  if (!reader) throw new Error('QQ response has no body');
  const parts = [];
  let size = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) return Buffer.concat(parts);
      size += value.byteLength;
      if (size > maxBytes) throw new Error('QQ response exceeds the size limit');
      parts.push(Buffer.from(value));
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

export class QQApi {
  constructor({ appId, appSecret }, { fetchImpl = globalThis.fetch } = {}) {
    credentials(appId, appSecret);
    this.appId = appId;
    this.appSecret = appSecret;
    this.fetchImpl = fetchImpl;
    this.clearToken();
  }

  clearToken() { this.token = null; this.expiresAt = 0; }

  async json(endpoint, body, signal, token) {
    try {
      signal.throwIfAborted();
      const response = await this.fetchImpl(`${API_BASE}${endpoint}`, {
        method: body === undefined ? 'GET' : 'POST', redirect: 'error', signal,
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `QQBot ${token}` } : {}) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      const result = JSON.parse((await readBounded(response, 1024 * 1024)).toString('utf8'));
      if (!response.ok || [201, 202].includes(response.status)
        || !result || typeof result !== 'object' || Array.isArray(result)
        || (result.code !== undefined && Number(result.code) !== 0)
        || (result.err_code !== undefined && Number(result.err_code) !== 0)) {
        throw new QQError(response.status, result?.code ?? result?.err_code);
      }
      signal.throwIfAborted();
      return result;
    } catch (error) {
      if (signal.aborted) throw new Error('QQ request cancelled or timed out');
      if (error instanceof QQError) throw error;
      // No provider descriptions, URLs, bodies or fetch causes reach the logs.
      throw new Error('QQ request failed or returned invalid data');
    }
  }

  async accessToken(signal = AbortSignal.timeout(15_000)) {
    if (this.token && Date.now() < this.expiresAt) return this.token;
    const result = await this.json('/app/getAppAccessToken', { appId: this.appId, clientSecret: this.appSecret }, signal);
    const lifetime = Number(result.expires_in);
    if (!Number.isFinite(lifetime) || lifetime <= 0 || lifetime > 86400) throw new Error('Invalid QQ token lifetime');
    this.token = identifier(result.access_token, 'access token', 4096);
    this.expiresAt = Date.now() + Math.max(0, lifetime - 60) * 1000;
    return this.token;
  }

  async request(endpoint, body, signal = AbortSignal.timeout(30_000)) {
    if (endpoint !== '/gateway' && !/^\/v2\/users\/[A-Za-z0-9_-]{1,256}\/(messages|files|upload_prepare|upload_part_finish)$/.test(endpoint)) {
      throw new Error('Unsupported QQ API endpoint');
    }
    // No automatic retries: a failed send can already have reached the owner.
    return this.json(endpoint, body, signal, await this.accessToken(signal));
  }

  async gateway(signal) {
    const result = await this.request('/gateway', undefined, signal);
    let url;
    try { url = new URL(result.url); } catch { throw new Error('Invalid QQ gateway URL'); }
    if (url.protocol !== 'wss:' || !url.hostname.endsWith('.qq.com') || url.username || url.password
      || url.port && url.port !== '443' || url.hash) throw new Error('Invalid QQ gateway URL');
    return url.href;
  }
}
