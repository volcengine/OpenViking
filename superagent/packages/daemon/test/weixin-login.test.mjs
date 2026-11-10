// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import test from 'node:test';
import { loginWeixin } from '../src/weixin/auth.mjs';

test('a cancelled Weixin login binds nothing, even when the phone confirms afterwards', async (t) => {
  const original = globalThis.fetch;
  t.after(() => { globalThis.fetch = original; });
  const controller = new AbortController();
  globalThis.fetch = async (url) => {
    if (String(url).includes('get_bot_qrcode')) return new Response(JSON.stringify({ qrcode: 'q', qrcode_img_content: 'https://weixin.example/q' }));
    // The owner goes back while this poll is out; the phone confirms anyway.
    controller.abort();
    return new Response(JSON.stringify({ status: 'confirmed', bot_token: 'token', ilink_bot_id: 'bot' }));
  };
  const saved = [];
  const shown = [];
  const state = { account: () => null, saveAccount: (account) => { saved.push(account); return account; } };
  await assert.rejects(loginWeixin({ state, signal: controller.signal, showQr: (value) => { shown.push(value); }, say() {} }), { name: 'AbortError' });
  assert.deepEqual(shown, ['https://weixin.example/q']);
  assert.deepEqual(saved, []);
});
