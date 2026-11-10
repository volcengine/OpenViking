// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { PassThrough } from 'node:stream';
import { setTimeout as delay } from 'node:timers/promises';
import test from 'node:test';
import { connectQQ } from '../src/qq.mjs';
import { Prompter } from '../src/prompt.mjs';
import { setLanguage } from '../src/i18n.mjs';
import { QQState } from '../../daemon/src/qq/state.mjs';

const SECRET = 'secret_that_must_never_be_logged';
function fixture(t, answers, { tty = false } = {}) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-qq-ui-'));
  const state = new QQState(dataDir);
  state.saveAccount({ appId: '123456', appSecret: 'previous_private_secret', ownerId: 'OLD_OWNER', activatedAt: 0 });
  const input = new PassThrough();
  if (tty) { input.isTTY = true; input.setRawMode = () => {}; }
  else input.end(answers.join('\n') + '\n');
  const output = new PassThrough(); let text = '';
  output.on('data', (data) => { text += data; });
  t.after(() => { state.close(); fs.rmSync(dataDir, { recursive: true, force: true }); });
  return { state, input, output, text: () => text, options: { plan: { dataDir }, prompter: new Prompter({ input, output }) } };
}
const provider = async (url) => Response.json(url.endsWith('/app/getAppAccessToken')
  ? { access_token: 'token', expires_in: '7200' } : { url: 'wss://api.sgroup.qq.com/' });

for (const language of ['zh', 'en']) test(`QQ UI retries invalid credentials, hides AppSecret and commits only after private challenge proof (${language})`, async (t) => {
  setLanguage(language);
  t.after(() => setLanguage('zh'));
  const f = fixture(t, ['123456', SECRET + '_INVALID', '1', '123456', SECRET]);
  f.options.fetchImpl = async (url, init) => {
    assert.equal(f.state.account().ownerId, 'OLD_OWNER');
    return init.body?.includes('_INVALID') ? Response.json({ code: 100007, message: SECRET }) : provider(url);
  };
  f.options.gatewayFactory = ({ signal, onReady, onMessage }) => ({ async start() {
    onReady(); await delay(0);
    const challenge = f.text().match(/vika-bind [a-f0-9]{36}/)[0];
    onMessage({ kind: 'c2c', rawEventType: 'C2C_MESSAGE_CREATE', senderId: 'NEW_OWNER',
      messageId: 'proof', content: challenge, timestamp: new Date().toISOString() });
    await new Promise((resolve) => signal.addEventListener('abort', resolve, { once: true }));
  } });
  await connectQQ(f.options);
  // A bot can only be created after registering as a QQ Open Platform developer.
  const register = f.text().indexOf('https://q.qq.com/#/');
  assert.ok(register >= 0 && register < f.text().indexOf('https://q.qq.com/qqbot/openclaw/'));
  assert.equal(f.state.account().appSecret, SECRET);
  assert.equal(f.state.account().ownerId, 'NEW_OWNER');
  if (language === 'zh') assert.match(f.text(), /100007[\s\S]*等待 QQ 私聊验证[\s\S]*QQ 已接好/);
  else {
    assert.match(f.text(), /100007[\s\S]*Waiting for QQ private-chat verification[\s\S]*QQ is connected/);
    assert.doesNotMatch(f.text(), /[一-鿿]/);
  }
  assert.ok(!f.text().includes(SECRET));
});

for (const canGoBack of [true, false]) test(`QQ failed verification leaves old account intact (back=${canGoBack})`, async (t) => {
  const f = fixture(t, ['123456', SECRET, '2']);
  f.options.fetchImpl = async () => Response.json({ code: 100007, message: SECRET });
  await assert.rejects(connectQQ({ ...f.options, canGoBack }), { name: canGoBack ? 'GoBack' : 'PromptAborted' });
  assert.equal(f.state.account().appSecret, 'previous_private_secret');
  f.state.acquire(); f.state.release();
  assert.ok(!f.text().includes(SECRET));
});

test('back while QQ waits for owner verification aborts the gateway and releases its lease', { timeout: 2000 }, async (t) => {
  const f = fixture(t, [], { tty: true });
  f.options.prompter.text = async () => '123456';
  f.options.prompter.secret = async () => SECRET;
  f.options.fetchImpl = provider;
  let aborted = false;
  f.options.gatewayFactory = ({ signal, onReady }) => ({ async start() {
    onReady(); await delay(0); f.input.emit('keypress', '', { name: 'left' });
    if (!signal.aborted) await new Promise((resolve) => signal.addEventListener('abort', resolve, { once: true }));
    aborted = signal.aborted;
  } });
  await assert.rejects(connectQQ({ ...f.options, canGoBack: true }), { name: 'GoBack' });
  assert.equal(aborted, true);
  assert.equal(f.state.account().ownerId, 'OLD_OWNER');
  f.state.acquire(); f.state.release();
});
