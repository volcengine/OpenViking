// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { FeishuError, createFeishuClient } from '../client.mjs';

const card = { config: { update_multi: true }, elements: [{ tag: 'hr' }] };

function fakeFetch(handler) {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    const reply = handler(url, options, calls.length) ?? { code: 0 };
    const { status = 200, body = reply } = reply;
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: 'x',
      text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
    };
  };
  return { calls, fetchImpl };
}

const token = (url) => url.endsWith('/tenant_access_token/internal');

describe('feishu client', () => {
  it('sends an interactive card and returns the message id', async () => {
    const { calls, fetchImpl } = fakeFetch((url) => (token(url)
      ? { code: 0, tenant_access_token: 't', expire: 7200 }
      : { code: 0, data: { message_id: 'om_sent1234567890' } }));
    const client = createFeishuClient({ appId: 'a', appSecret: 's', fetchImpl });
    const sent = await client.sendCard({ chatId: 'oc_1234567890abcdef', card });
    assert.equal(sent.data.message_id, 'om_sent1234567890');
    const { url, options } = calls.find((call) => call.url.includes('/im/v1/messages'));
    assert.match(url, /\/open-apis\/im\/v1\/messages\?receive_id_type=chat_id$/);
    const body = JSON.parse(options.body);
    assert.equal(body.receive_id, 'oc_1234567890abcdef');
    assert.equal(body.msg_type, 'interactive');
    assert.deepEqual(JSON.parse(body.content), card);
  });

  it('replies in the thread instead of posting a new message when given one', async () => {
    const { calls, fetchImpl } = fakeFetch((url) => (token(url)
      ? { code: 0, tenant_access_token: 't' }
      : { code: 0, data: { message_id: 'om_reply12345678' } }));
    const client = createFeishuClient({ appId: 'a', appSecret: 's', fetchImpl });
    await client.sendCard({ chatId: 'oc_1234567890abcdef', card, replyTo: 'om_parent12345678' });
    const reply = calls.find((call) => call.url.includes('/reply'));
    assert.match(reply.url, /\/messages\/om_parent12345678\/reply$/);
    assert.equal(JSON.parse(reply.options.body).msg_type, 'interactive');
  });

  it('patches with a JSON-encoded content field and caches the tenant token', async () => {
    const { calls, fetchImpl } = fakeFetch((url) => (token(url) ? { code: 0, tenant_access_token: 't-1', expire: 7200 } : { code: 0 }));
    const client = createFeishuClient({ appId: 'a', appSecret: 's', fetchImpl });
    await client.patchCard({ messageId: 'om_x1234567890', card });
    await client.patchCard({ messageId: 'om_x1234567890', card });
    assert.equal(calls.filter((call) => token(call.url)).length, 1);
    const patch = calls.find((call) => call.options.method === 'PATCH');
    assert.match(patch.url, /\/open-apis\/im\/v1\/messages\/om_x1234567890$/);
    assert.deepEqual(JSON.parse(patch.options.body), { content: JSON.stringify(card) });
    assert.equal(patch.options.headers.authorization, 'Bearer t-1');
  });

  it('reports the platform error instead of pretending the card went out', async () => {
    const { fetchImpl } = fakeFetch((url) => (token(url)
      ? { code: 0, tenant_access_token: 't' }
      : { code: 230001, msg: 'invalid receive_id' }));
    const client = createFeishuClient({ appId: 'a', appSecret: 's', fetchImpl, sleep: async () => {} });
    await assert.rejects(client.sendCard({ chatId: 'bad', card }),
      (error) => error instanceof FeishuError && error.code === 230001 && /invalid receive_id/.test(error.message));
  });

  it('retries a patch over a transient failure, because a repeated patch is one card', async () => {
    const slept = [];
    const { calls, fetchImpl } = fakeFetch((url, _options, count) => {
      if (token(url)) return { code: 0, tenant_access_token: 't' };
      return count === 2 ? { status: 500, body: { code: 230099, msg: 'server' } } : { code: 0 };
    });
    const client = createFeishuClient({ appId: 'a', appSecret: 's', fetchImpl, sleep: async (ms) => { slept.push(ms); } });
    await client.patchCard({ messageId: 'om_x1234567890', card });
    assert.deepEqual(slept, [300]);
    assert.equal(calls.filter((call) => call.options.method === 'PATCH').length, 2);
  });

  it('does not retry a send on 5xx, which may have delivered the card already', async () => {
    const { calls, fetchImpl } = fakeFetch((url) => (token(url)
      ? { code: 0, tenant_access_token: 't' }
      : { status: 500, body: { code: 230099, msg: 'server' } }));
    const client = createFeishuClient({ appId: 'a', appSecret: 's', fetchImpl, sleep: async () => {} });
    await assert.rejects(client.sendCard({ chatId: 'oc_1234567890abcdef', card }), FeishuError);
    assert.equal(calls.filter((call) => call.url.includes('/im/v1/messages')).length, 1);
  });

  it('retries a send the platform refused with 429, which means nothing was delivered', async () => {
    const { calls, fetchImpl } = fakeFetch((url, _options, count) => {
      if (token(url)) return { code: 0, tenant_access_token: 't' };
      return count === 2 ? { status: 429, body: { code: 99991400, msg: 'rate limited' } } : { code: 0, data: { message_id: 'om_x' } };
    });
    const client = createFeishuClient({ appId: 'a', appSecret: 's', fetchImpl, sleep: async () => {} });
    await client.sendCard({ chatId: 'oc_1234567890abcdef', card });
    assert.equal(calls.filter((call) => call.url.includes('/im/v1/messages')).length, 2);
  });

  it('treats a Feishu rate-limit code on an HTTP 400 as retryable', async () => {
    const { calls, fetchImpl } = fakeFetch((url, _options, count) => {
      if (token(url)) return { code: 0, tenant_access_token: 't' };
      return count === 2
        ? { status: 400, body: { code: 99991400, msg: 'request trigger frequency limit' } }
        : { code: 0, data: { message_id: 'om_x' } };
    });
    const client = createFeishuClient({ appId: 'a', appSecret: 's', fetchImpl, sleep: async () => {} });
    await client.sendCard({ chatId: 'oc_1234567890abcdef', card });
    assert.equal(calls.filter((call) => call.url.includes('/im/v1/messages')).length, 2);
  });

  it('fails loudly when the token response carries no token', async () => {
    const { fetchImpl } = fakeFetch(() => ({ code: 0 }));
    const client = createFeishuClient({ appId: 'a', appSecret: 's', fetchImpl, sleep: async () => {} });
    await assert.rejects(client.patchCard({ messageId: 'om_x1234567890', card }), /no token/);
  });

  it('refuses an oversized card before calling the platform', async () => {
    const client = createFeishuClient({ appId: 'a', appSecret: 's', fetchImpl: () => assert.fail('no request'),
      sleep: async () => {} });
    await assert.rejects(client.patchCard({ messageId: 'om_x1234567890',
      card: { elements: [{ tag: 'div', text: { tag: 'plain_text', content: 'x'.repeat(40_000) } }] } }),
    (error) => error instanceof FeishuError && /over the 30720 limit/.test(error.message));
  });

  it('records calls without a network in dry run', async () => {
    const client = createFeishuClient({ dryRun: true, fetchImpl: () => assert.fail('no network in dry run') });
    const sent = await client.sendCard({ chatId: 'oc_1234567890abcdef', card });
    assert.equal(sent.dryRun, true);
    assert.deepEqual(client.calls.map((call) => call.name), ['send']);
  });

  it('refuses a card without elements before calling the platform', async () => {
    const client = createFeishuClient({ dryRun: true });
    await assert.rejects(client.patchCard({ messageId: 'om_x1234567890', card: { config: {} } }), FeishuError);
  });
});
