// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The two calls a status card needs: send it once, then patch the same message.
//
// Deliberately small: the tenant token, one send and one patch, no SDK. The
// card JSON is the only format that matters here, and an SDK would add a model
// to translate to and from without covering the patch call any better.
//
// Every call takes the card object from card.mjs and serialises it the way the
// platform expects: `content` is a JSON string inside the JSON body.

const DEFAULT_BASE = 'https://open.feishu.cn';

function assertCard(card) {
  if (!card || typeof card !== 'object' || !Array.isArray(card.elements)) {
    throw new Error('card must be an interactive card object with elements');
  }
}

export function createFeishuClient({
  appId,
  appSecret,
  baseUrl = DEFAULT_BASE,
  fetchImpl = globalThis.fetch,
  dryRun = false,
  now = Date.now,
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
    try { parsed = text ? JSON.parse(text) : {}; } catch { throw new Error(`feishu ${path}: non-JSON response (${response.status})`); }
    if (!response.ok || (parsed.code !== undefined && parsed.code !== 0)) {
      throw new Error(`feishu ${path}: code=${parsed.code ?? response.status} msg=${parsed.msg ?? response.statusText}`);
    }
    return parsed;
  }

  async function tenantToken() {
    if (dryRun) return 'dry-run-token';
    if (token && token.expiresAt > now() + 60_000) return token.value;
    const data = await request('/open-apis/auth/v3/tenant_access_token/internal', {
      body: { app_id: appId, app_secret: appSecret },
    });
    token = { value: data.tenant_access_token, expiresAt: now() + (data.expire ?? 7200) * 1000 };
    return token.value;
  }

  async function call(name, path, options) {
    if (dryRun) {
      calls.push({ name, path, options });
      return { code: 0, dryRun: true, data: { message_id: `om_dryrun_${calls.length}` } };
    }
    const auth = { authorization: `Bearer ${await tenantToken()}` };
    const data = await request(path, { ...options, headers: auth });
    calls.push({ name, path, options });
    return data;
  }

  return {
    calls,
    tenantToken,
    // Reply in the thread the task came from when there is one; otherwise post
    // to the chat. Both return the message_id the patch below needs.
    async sendCard({ chatId, card, replyTo = null }) {
      assertCard(card);
      return replyTo
        ? call('reply', `/open-apis/im/v1/messages/${encodeURIComponent(replyTo)}/reply`, {
          body: { msg_type: 'interactive', content: JSON.stringify(card) },
        })
        : call('send', '/open-apis/im/v1/messages', {
          query: `?receive_id_type=chat_id`,
          body: { receive_id: chatId, msg_type: 'interactive', content: JSON.stringify(card) },
        });
    },
    // Replaces the whole card. `update_multi` in the card config is what makes
    // Feishu apply the new rendering instead of keeping the first one.
    async patchCard({ messageId, card }) {
      assertCard(card);
      return call('patch', `/open-apis/im/v1/messages/${encodeURIComponent(messageId)}`, {
        method: 'PATCH',
        body: { content: JSON.stringify(card) },
      });
    },
  };
}
