// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { createFeishuClient } from '../client.mjs';

const card = { config: { update_multi: true }, elements: [{ tag: 'hr' }] };

function fakeFetch(handler) {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    const body = handler(url, options);
    return { ok: (body.code ?? 0) === 0, status: 200, statusText: 'OK', text: async () => JSON.stringify(body) };
  };
  return { calls, fetchImpl };
}

describe('feishu client', () => {
  it('sends an interactive card and returns the message id', async () => {
    const { calls, fetchImpl } = fakeFetch(() => ({ code: 0, data: { message_id: 'om_sent1234567890' } }));
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
    const { calls, fetchImpl } = fakeFetch(() => ({ code: 0, data: { message_id: 'om_reply12345678' } }));
    const client = createFeishuClient({ appId: 'a', appSecret: 's', fetchImpl });
    await client.sendCard({ chatId: 'oc_1234567890abcdef', card, replyTo: 'om_parent12345678' });
    const reply = calls.find((call) => call.url.includes('/reply'));
    assert.match(reply.url, /\/messages\/om_parent12345678\/reply$/);
    assert.equal(JSON.parse(reply.options.body).msg_type, 'interactive');
  });

  it('patches with a JSON-encoded content field and caches the tenant token', async () => {
    const { calls, fetchImpl } = fakeFetch((url) => (url.endsWith('/tenant_access_token/internal')
      ? { code: 0, tenant_access_token: 't-1', expire: 7200 }
      : { code: 0, data: {} }));
    const client = createFeishuClient({ appId: 'a', appSecret: 's', fetchImpl });
    await client.patchCard({ messageId: 'om_x1234567890', card });
    await client.patchCard({ messageId: 'om_x1234567890', card });
    const tokenCalls = calls.filter((call) => call.url.endsWith('/tenant_access_token/internal'));
    assert.equal(tokenCalls.length, 1);
    const patch = calls.find((call) => call.options.method === 'PATCH');
    assert.match(patch.url, /\/open-apis\/im\/v1\/messages\/om_x1234567890$/);
    assert.deepEqual(JSON.parse(patch.options.body), { content: JSON.stringify(card) });
    assert.equal(patch.options.headers.authorization, 'Bearer t-1');
  });

  it('reports the platform error instead of pretending the card went out', async () => {
    const { fetchImpl } = fakeFetch(() => ({ code: 230001, msg: 'invalid receive_id' }));
    const client = createFeishuClient({ appId: 'a', appSecret: 's', fetchImpl });
    await assert.rejects(client.sendCard({ chatId: 'bad', card }), /code=230001 msg=invalid receive_id/);
  });

  it('records calls without a network in dry run', async () => {
    const client = createFeishuClient({ dryRun: true, fetchImpl: () => assert.fail('no network in dry run') });
    const sent = await client.sendCard({ chatId: 'oc_1234567890abcdef', card });
    assert.equal(sent.dryRun, true);
    assert.deepEqual(client.calls.map((call) => call.name), ['send']);
  });

  it('refuses a card without elements before calling the platform', async () => {
    const client = createFeishuClient({ dryRun: true });
    await assert.rejects(client.patchCard({ messageId: 'om_x1234567890', card: { config: {} } }), /elements/);
  });
});
