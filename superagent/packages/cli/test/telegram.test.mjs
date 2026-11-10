// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { connectTelegram } from '../src/telegram.mjs';
import { Prompter } from '../src/prompt.mjs';
import { TelegramState } from '../../daemon/src/telegram/state.mjs';

const TOKEN = '123456:abcdefghijklmnopqrstuvwxyz_TEST';
function fixture(t, answers) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-telegram-ui-'));
  const state = new TelegramState(dataDir);
  state.saveAccount({ botId: '123456', ownerId: '99', chatId: '99', token: 'old-key', offset: 13, activatedAt: 0 });
  const input = new PassThrough(); input.end(answers.join('\n') + '\n');
  const output = new PassThrough();
  let text = ''; output.on('data', (data) => { text += data; });
  t.after(() => { state.close(); fs.rmSync(dataDir, { recursive: true, force: true }); });
  return { state, text: () => text, options: { plan: { dataDir }, prompter: new Prompter({ input, output }) } };
}

test('invalid token can be retried, hides credentials, and commits only after private Start verification', async (t) => {
  const f = fixture(t, [TOKEN + '_INVALID', '1', TOKEN]);
  f.options.fetchImpl = async (url, init) => {
    assert.equal(f.state.account().token, 'old-key');
    if (url.includes('_INVALID/')) return Response.json({ ok: false, error_code: 401, description: TOKEN }, { status: 401 });
    if (url.endsWith('/getMe')) return Response.json({ ok: true, result: { id: 123456, is_bot: true, username: 'vika_test_bot' } });
    if (url.endsWith('/getWebhookInfo')) return Response.json({ ok: true, result: { url: '' } });
    const nonce = f.text().match(/https:\/\/t\.me\/vika_test_bot\?start=([A-Za-z0-9_-]+)/)[1];
    assert.equal(JSON.parse(init.body).offset, 13);
    return Response.json({ ok: true, result: [{ update_id: 13, message: { message_id: 13, date: Math.floor(Date.now() / 1000),
      from: { id: 99, is_bot: false }, chat: { id: 99, type: 'private' }, text: `/start ${nonce}` } }] });
  };
  await connectTelegram(f.options);
  assert.equal(f.state.account().token, TOKEN);
  assert.equal(f.state.account().offset, 14);
  assert.match(f.text(), /token 无效或已撤销[\s\S]*点 Start[\s\S]*Telegram 已接好/);
  assert.ok(!f.text().includes(TOKEN));
});

for (const canGoBack of [true, false]) test(`webhook conflict leaves existing credentials intact (back=${canGoBack})`, async (t) => {
  const f = fixture(t, [TOKEN, '2']);
  f.options.fetchImpl = async (url) => Response.json({ ok: true, result: url.endsWith('/getMe')
    ? { id: 123456, is_bot: true, username: 'vika_test_bot' } : { url: 'https://old-service.example' } });
  await assert.rejects(connectTelegram({ ...f.options, canGoBack }), { name: canGoBack ? 'GoBack' : 'PromptAborted' });
  assert.equal(f.state.account().token, 'old-key');
  f.state.acquire(); f.state.release();
  assert.match(f.text(), /已配置 Webhook/);
  assert.ok(!f.text().includes(TOKEN));
});
