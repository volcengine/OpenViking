// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { setTimeout as delay } from 'node:timers/promises';

// https://docs.slack.dev/apis/web-api/ — one workspace-internal app per
// instance: a bot token (xoxb-) for the Web API and an app-level token
// (xapp-) that only opens Socket Mode connections.
export const API_BASE = 'https://slack.com/api';
export const SLACK_VERSION = 'slack-socket-mode/v1';
// chat.postMessage truncates past 40,000 characters and Slack recommends far
// less; longer output goes as a file.
export const TEXT_LIMIT = 12_000;
// The Core's own workspace-file limit, for downloads as well.
export const FILE_BYTES = 20 * 1024 * 1024;

const BOT_TOKEN = /^xoxb-[A-Za-z0-9-]{10,250}$/;
const APP_TOKEN = /^xapp-[A-Za-z0-9-]{10,250}$/;
const METHOD = /^[a-z]+(?:\.[a-zA-Z]+)+$/;
// Errors that mean the token or the app no longer works: retrying cannot help.
const AUTH_ERRORS = new Set(['invalid_auth', 'not_authed', 'account_inactive', 'token_revoked', 'token_expired',
  'not_allowed_token_type', 'no_permission', 'missing_scope', 'ekm_access_denied', 'team_access_not_granted']);

export class SlackError extends Error {
  constructor(method, error, status = 200, retryAfter = null) {
    const code = typeof error === 'string' && /^[a-z0-9_]{1,64}$/.test(error) ? error : 'unknown_error';
    super(`Slack ${method} failed: ${code}`);
    this.method = method;
    this.code = code;
    this.status = status;
    this.retryAfter = Number.isSafeInteger(retryAfter) && retryAfter >= 0 ? retryAfter : null;
  }

  get auth() { return AUTH_ERRORS.has(this.code); }
}

export function validToken(token, kind) {
  return typeof token === 'string' && (kind === 'app' ? APP_TOKEN : BOT_TOKEN).test(token);
}

export async function readBounded(response, maxBytes) {
  const reader = response.body?.getReader();
  if (!reader) throw new Error('Slack response has no body');
  const parts = [];
  let length = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > maxBytes) throw new Error('Slack response exceeds the size limit');
      parts.push(Buffer.from(value));
    }
    return Buffer.concat(parts);
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

// Only Slack's own hosts ever see a token or serve bytes to this process.
export function slackHost(url, protocol = 'https:') {
  try {
    const parsed = new URL(url);
    return parsed.protocol === protocol && !parsed.username && !parsed.password
      && (parsed.hostname === 'slack.com' || parsed.hostname.endsWith('.slack.com'));
  } catch { return false; }
}

export class SlackApi {
  constructor(token, { kind = 'bot', fetchImpl = globalThis.fetch } = {}) {
    if (!validToken(token, kind)) throw new Error(kind === 'app' ? 'Invalid Slack app-level token' : 'Invalid Slack bot token');
    this.token = token;
    this.fetchImpl = fetchImpl;
  }

  // Form-encoded, which every Web API method accepts; the token travels only
  // in the Authorization header.
  async call(method, params = {}, signal = AbortSignal.timeout(30_000)) {
    if (!METHOD.test(method)) throw new Error('Unsupported Slack API method');
    const body = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null) continue;
      body.set(key, typeof value === 'object' ? JSON.stringify(value) : String(value));
    }
    let response;
    let result;
    try {
      signal.throwIfAborted();
      response = await this.fetchImpl(`${API_BASE}/${method}`, {
        method: 'POST', redirect: 'error', signal,
        headers: { Authorization: `Bearer ${this.token}`, 'Content-Type': 'application/x-www-form-urlencoded' },
        body,
      });
      try { result = JSON.parse((await readBounded(response, 4 * 1024 * 1024)).toString('utf8')); }
      catch { result = null; }
    } catch {
      // Never propagate fetch errors or their causes: they may carry headers.
      if (signal.aborted) throw new Error('Slack request cancelled or timed out');
      throw new Error('Slack request failed');
    }
    if (response.status === 429 || result?.error === 'ratelimited') {
      throw new SlackError(method, 'ratelimited', response.status, Number.parseInt(response.headers.get('retry-after') ?? '', 10));
    }
    if (!result || typeof result !== 'object') throw new Error(`Slack ${method} returned an invalid response (HTTP ${response.status})`);
    if (result.ok !== true) throw new SlackError(method, result.error, response.status);
    return result;
  }

  // Only an explicit rate limit is safe to retry: a send that failed any
  // other way may have gone out, and Slack has no idempotency key.
  async send(method, params, signal) {
    for (let attempt = 0; ; attempt += 1) {
      try { return await this.call(method, params, signal); } catch (error) {
        if (attempt >= 2 || !(error instanceof SlackError) || error.code !== 'ratelimited' || error.retryAfter === null) throw error;
        await delay(Math.max(1, error.retryAfter) * 1000, undefined, { signal });
      }
    }
  }

  // Who the bot token is: its workspace, bot user and bot, and the app behind it.
  async identity(signal) {
    const auth = await this.call('auth.test', {}, signal);
    if (!/^T[A-Z0-9]{2,30}$/.test(auth.team_id ?? '') || !/^[UW][A-Z0-9]{2,30}$/.test(auth.user_id ?? '')
      || !/^B[A-Z0-9]{2,30}$/.test(auth.bot_id ?? '')) throw new Error('Invalid Slack bot identity');
    const { bot } = await this.call('bots.info', { bot: auth.bot_id }, signal);
    if (!/^A[A-Z0-9]{2,30}$/.test(bot?.app_id ?? '')) throw new Error('Invalid Slack app identity');
    return { teamId: auth.team_id, team: String(auth.team ?? ''), botUserId: auth.user_id, botId: auth.bot_id,
      appId: bot.app_id, botName: String(bot.name ?? '') };
  }

  async connectionUrl(signal) {
    const { url } = await this.call('apps.connections.open', {}, signal);
    if (!slackHost(url, 'wss:')) throw new Error('Slack returned an unexpected Socket Mode address');
    return url;
  }

  // A file goes up in three steps: an upload URL, the bytes, then sharing it.
  async upload({ bytes, name, channel, threadTs = null }, signal) {
    const { upload_url: url, file_id: id } = await this.send('files.getUploadURLExternal',
      { filename: name, length: bytes.length }, signal);
    if (!slackHost(url) || !/^F[A-Z0-9]{2,30}$/.test(id ?? '')) throw new Error('Slack returned an unexpected upload address');
    const form = new FormData();
    form.set('file', new Blob([bytes]), name);
    let response;
    try {
      response = await this.fetchImpl(url, { method: 'POST', redirect: 'error', signal, body: form });
      await response.body?.cancel();
    } catch {
      if (signal.aborted) throw new Error('Slack request cancelled or timed out');
      throw new Error('Slack file upload failed');
    }
    if (!response.ok) throw new Error(`Slack file upload failed (HTTP ${response.status})`);
    await this.send('files.completeUploadExternal', { files: [{ id, title: name }], channel_id: channel,
      ...(threadTs ? { thread_ts: threadTs } : {}) }, signal);
    return id;
  }

  async download(url, signal) {
    if (!slackHost(url)) throw new Error('Slack file address is not on Slack');
    try {
      signal.throwIfAborted();
      const response = await this.fetchImpl(url, { redirect: 'error', signal,
        headers: { Authorization: `Bearer ${this.token}` } });
      // Without access Slack answers with its sign-in page instead of the file.
      if (!response.ok || /text\/html/i.test(response.headers.get('content-type') ?? '')) {
        await response.body?.cancel();
        throw new Error('download rejected');
      }
      const bytes = await readBounded(response, FILE_BYTES);
      signal.throwIfAborted();
      return bytes;
    } catch { throw new Error('Slack file download failed or exceeded the size limit'); }
  }
}
