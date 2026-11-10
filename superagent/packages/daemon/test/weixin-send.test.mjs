// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { it } from 'node:test';
import { sendWeixinMessage, weixinText } from '../src/weixin/send.mjs';

function state({ written = true } = {}) {
  return {
    account: () => ({ token: 'bot-token', baseUrl: 'https://weixin.example', userId: 'peer@im.wechat' }),
    contextToken: () => (written ? 'context-token' : null),
  };
}

const text = (value) => ({ type: 'markdown', text: value });

it('derives the Weixin client id from the Core idempotency key, so a retry stays one message', async (t) => {
  const bodies = [];
  t.mock.method(globalThis, 'fetch', async (_url, init) => {
    bodies.push(JSON.parse(init.body));
    return new Response(JSON.stringify({ ret: 0, message_id: 'remote-1' }));
  });
  const send = (key) => sendWeixinMessage({ state: state(), to: 'peer@im.wechat', content: text('hi'), idempotencyKey: key });
  const first = await send('ld_same');
  const retry = await send('ld_same');
  const other = await send('ld_other');
  assert.equal(first.message_id, 'remote-1');
  assert.equal(first.client_id, retry.client_id);
  assert.notEqual(first.client_id, other.client_id);
  assert.equal(bodies[0].msg.client_id, first.client_id);
  assert.equal(bodies[0].msg.context_token, 'context-token');
  assert.deepEqual(bodies[0].msg.item_list, [{ type: 1, text_item: { text: 'hi' } }]);
});

it('keeps every line break visible on Weixin, which shows text the way Markdown does', () => {
  assert.equal(weixinText('标题\n第一行\n\n第二段\n```\na\nb\n```\n完'), '标题\n\n第一行\n\n第二段\n\n```\na\nb\n```\n\n完');
  assert.equal(weixinText('一行'), '一行');
});

it('rejects anyone but the owner, an owner who has not written yet, unsupported bodies and oversized text', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => assert.fail('must not call Weixin'));
  const send = (fields) => sendWeixinMessage({ state: state(), to: 'peer@im.wechat', content: text('hi'), idempotencyKey: 'k', ...fields });
  await assert.rejects(() => send({ to: 'someone@im.wechat' }), /only message the account that scanned the login QR code/);
  // Weixin would take it and never deliver it.
  await assert.rejects(() => send({ state: state({ written: false }) }), /has not written to this bot since it logged in/);
  await assert.rejects(() => send({ content: { type: 'card', card: {} } }), /does not support card messages/);
  await assert.rejects(() => send({ content: text('x'.repeat(4_001)) }), /4000-character limit/);
  assert.equal(fetch.mock.callCount(), 0);
});
