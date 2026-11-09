// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { PassThrough } from 'node:stream';
import { setTimeout as delay } from 'node:timers/promises';
import test from 'node:test';
import { connectOneBot } from '../src/onebot.mjs';
import { setLanguage } from '../src/i18n.mjs';
import { Prompter } from '../src/prompt.mjs';
import { OneBotState } from '../../daemon/src/onebot/state.mjs';

const URL_ = 'ws://127.0.0.1:3001/';
const TOKEN = 'napcat-Secret_123';
const SELF = '10001';
const OWNER = '20002';
const OLD = Object.freeze({ url: 'ws://old.example:3001/', token: 'old-token', selfId: SELF, selfName: 'Old', ownerId: '30003', activatedAt: 1 });
const GROUPS = [{ group_id: 123456789, group_name: 'Friends' }, { group_id: 987654321, group_name: 'Work' }];

function fixture(t, answers, { tty = false } = {}) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-ob-ui-'));
  const state = new OneBotState(dataDir);
  state.saveAccount(OLD);
  const input = new PassThrough();
  if (tty) { input.isTTY = true; input.setRawMode = () => {}; }
  else input.end(answers.join('\n') + '\n');
  const output = new PassThrough();
  let text = ''; output.on('data', (data) => { text += data; });
  t.after(() => { state.close(); fs.rmSync(dataDir, { recursive: true, force: true }); });
  return { dataDir, state, input, text: () => text, options: { plan: { dataDir }, prompter: new Prompter({ input, output }) } };
}

const ok = (data, echo) => ({ status: 'ok', retcode: 0, data, message: '', wording: '', echo });

// A OneBot WebSocket server for one account. It refuses another token the
// way NapCat does, and once the CLI shows the code, the owner sends it in a
// private friend message (unless `deliver` is off).
function server(text, { groups = GROUPS, deliver = true, onCode = () => {} } = {}) {
  const opened = [];
  class Socket extends EventTarget {
    constructor(url, init) {
      super();
      Object.assign(this, { url, init, readyState: 0 });
      opened.push(this);
      setImmediate(async () => {
        this.readyState = 1;
        this.dispatchEvent(new Event('open'));
        if (init?.headers?.Authorization !== `Bearer ${TOKEN}`) {
          this.frame({ status: 'failed', retcode: 1403, data: null, message: 'token验证失败', wording: 'token验证失败', echo: null });
          return;
        }
        this.frame({ time: 1, self_id: Number(SELF), post_type: 'meta_event', meta_event_type: 'lifecycle', sub_type: 'connect' });
        const deadline = Date.now() + 3000;
        let code;
        while (this.readyState === 1 && !(code = text().match(/vika-[a-z2-9]{8}/)?.[0]) && Date.now() < deadline) await delay(5);
        onCode(this);
        if (!deliver) return;
        this.frame({ post_type: 'message', message_type: 'private', sub_type: 'friend', self_id: Number(SELF), user_id: Number(OWNER),
          time: Math.floor(Date.now() / 1000), message_id: -123, message: [{ type: 'text', data: { text: code } }],
          sender: { user_id: Number(OWNER), nickname: 'Owner' } });
      });
    }

    send(data) {
      const { action, echo } = JSON.parse(data);
      const answer = { get_login_info: ok({ user_id: Number(SELF), nickname: 'Vika' }), get_group_list: ok(groups) }[action];
      setImmediate(() => this.frame({ ...(answer ?? { status: 'failed', retcode: 1404, data: null }), echo }));
    }

    close(code = 1005) {
      if (this.readyState === 3) return;
      this.readyState = 3;
      this.dispatchEvent(Object.assign(new Event('close'), { code }));
    }

    frame(value) {
      if (this.readyState === 1) this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(value) }));
    }
  }
  return { Socket, opened };
}

for (const language of ['zh', 'en']) test(`the address and token are checked, the token hidden, and the account saved only after the owner sends the code (${language})`, async (t) => {
  setLanguage(language);
  t.after(() => setLanguage('zh'));
  const f = fixture(t, ['http://127.0.0.1:3001', `${URL_}?access_token=${TOKEN}`, URL_, '', TOKEN]);
  const ws = server(f.text, { onCode: () => assert.deepEqual(f.state.account(), OLD) });
  // The token is asked with the hidden prompt, and nothing else is.
  const { prompter } = f.options;
  const secret = prompter.secret.bind(prompter);
  const hidden = [];
  prompter.secret = async (question) => { const value = await secret(question); hidden.push([question, value]); return value; };
  await connectOneBot({ ...f.options, WebSocketImpl: ws.Socket });
  assert.deepEqual(hidden, [['OneBot access token', ''], ['OneBot access token', TOKEN]]);
  assert.deepEqual(f.state.account(), { url: URL_, token: TOKEN, selfId: SELF, selfName: 'Vika', ownerId: OWNER, activatedAt: 1 });
  assert.equal(ws.opened.length, 1);
  assert.equal(ws.opened[0].url, URL_);
  assert.equal(ws.opened[0].readyState, 3);
  const policy = path.join(f.dataDir, 'onebot', 'config.json');
  assert.deepEqual(JSON.parse(fs.readFileSync(policy, 'utf8')), { groups: {} });
  assert.equal(fs.statSync(policy).mode & 0o777, 0o600);
  const text = f.text();
  // The address saved before is offered again.
  assert.ok(text.includes('(ws://old.example:3001/)'));
  assert.ok(text.includes(policy));
  assert.match(text, /\n {3}123456789 {2}Friends\n {3}987654321 {2}Work\n/);
  assert.match(text, /\{ "groups": \{ "123456789": "mention" \} \}/);
  if (language === 'zh') {
    assert.match(text, /以 ws:\/\/ 或 wss:\/\/ 开头[\s\S]*以 ws:\/\/ 或 wss:\/\/ 开头[\s\S]*access token 不能为空[\s\S]*私聊 Vika（10001）[\s\S]*QQ（OneBot） 已接好（Vika，10001）[\s\S]*没写进 "groups" 的群都不接。"mention" 只在有人 @ 它、回复它，或说到 "names" 里列出的某个名字时回应/);
  } else {
    assert.match(text, /starts with ws:\/\/ or wss:\/\/[\s\S]*cannot be empty[\s\S]*to Vika \(10001\) in a private QQ message[\s\S]*QQ \(OneBot\) is connected \(Vika, 10001\)[\s\S]*not listed under "groups" is not connected\. "mention" answers only when someone @mentions it, replies to it or uses one of the names listed under "names"/);
    assert.doesNotMatch(text, /[一-鿿]/);
  }
  assert.ok(!text.includes(TOKEN));
});

test('the owner\'s policy file is kept, and without a group list the owner is told where group numbers are', async (t) => {
  const f = fixture(t, [URL_, TOKEN]);
  const policy = path.join(f.dataDir, 'onebot', 'config.json');
  fs.writeFileSync(policy, '{"groups":{"123456789":"ambient"}}\n', { mode: 0o644 });
  await connectOneBot({ ...f.options, WebSocketImpl: server(f.text, { groups: [] }).Socket });
  assert.equal(f.state.account().ownerId, OWNER);
  assert.equal(fs.readFileSync(policy, 'utf8'), '{"groups":{"123456789":"ambient"}}\n');
  assert.equal(fs.statSync(policy).mode & 0o777, 0o644);
  assert.match(f.text(), /没有读到这个账号所在的群；群号可以在 QQ 的群资料里看到/);
});

for (const canGoBack of [true, false]) test(`a refused token keeps the old account and writes no policy file (back=${canGoBack})`, async (t) => {
  const f = fixture(t, [URL_, 'wrong-token', '2']);
  await assert.rejects(connectOneBot({ ...f.options, WebSocketImpl: server(f.text).Socket, canGoBack }),
    { name: canGoBack ? 'GoBack' : 'PromptAborted' });
  assert.deepEqual(f.state.account(), OLD);
  assert.equal(fs.existsSync(path.join(f.dataDir, 'onebot', 'config.json')), false);
  assert.match(f.text(), /OneBot 拒绝了这个 access token/);
  assert.ok(!f.text().includes('wrong-token'));
});

test('a retry offers the address typed last and binds with the corrected token', async (t) => {
  const f = fixture(t, [URL_, 'wrong-token', '1', '', TOKEN]);
  await connectOneBot({ ...f.options, WebSocketImpl: server(f.text).Socket });
  assert.equal(f.state.account().url, URL_);
  assert.equal(f.state.account().token, TOKEN);
  assert.ok(f.text().includes(`(${URL_})`));
  assert.match(f.text(), /OneBot 拒绝了这个 access token[\s\S]*QQ（OneBot） 已接好/);
});

test('back at the hidden token prompt leaves the step, as the hint says, and the typed token shows only as *', { timeout: 3000 }, async (t) => {
  const f = fixture(t, [], { tty: true });
  f.options.prompter.text = async () => URL_;
  const ws = server(f.text);
  const leaving = connectOneBot({ ...f.options, WebSocketImpl: ws.Socket, canGoBack: true });
  while (!f.text().includes('OneBot access token')) await delay(2);
  f.input.emit('keypress', 's', { name: 's' });
  f.input.emit('keypress', 'k', { name: 'k' });
  f.input.emit('keypress', '', { name: 'backspace' });
  f.input.emit('keypress', '', { name: 'backspace' });
  f.input.emit('keypress', '', { name: 'left' });
  await assert.rejects(leaving, { name: 'GoBack' });
  assert.match(f.text(), /按 ← \/ Esc 可以返回/);
  assert.match(f.text(), /OneBot access token: \*\*/);
  assert.doesNotMatch(f.text(), /sk/);
  assert.equal(ws.opened.length, 0);
  assert.deepEqual(f.state.account(), OLD);
});

test('back while waiting for the code closes the connection and keeps the old account', { timeout: 3000 }, async (t) => {
  const f = fixture(t, [], { tty: true });
  f.options.prompter.text = async () => URL_;
  f.options.prompter.secret = async () => TOKEN;
  const ws = server(f.text, { deliver: false, onCode: () => f.input.emit('keypress', '', { name: 'left' }) });
  await assert.rejects(connectOneBot({ ...f.options, WebSocketImpl: ws.Socket, canGoBack: true }), { name: 'GoBack' });
  assert.equal(ws.opened[0].readyState, 3);
  assert.deepEqual(f.state.account(), OLD);
});
