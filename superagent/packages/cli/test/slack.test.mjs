// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { PassThrough } from 'node:stream';
import { setTimeout as delay } from 'node:timers/promises';
import test from 'node:test';
import { appManifest, connectSlack, createAppUrl } from '../src/slack.mjs';
import { Prompter } from '../src/prompt.mjs';
import { SlackState } from '../../daemon/src/slack/state.mjs';

const BOT_TOKEN = 'xoxb-1111-2222-abcdefghijklmnop';
const APP_TOKEN = 'xapp-1-A0APP-3333-abcdefghijklmnop';

function fixture(t, answers) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-slack-ui-'));
  const input = new PassThrough(); input.end(answers.join('\n') + '\n');
  const output = new PassThrough();
  let text = ''; output.on('data', (data) => { text += data; });
  t.after(() => fs.rmSync(dataDir, { recursive: true, force: true }));
  return { dataDir, text: () => text, prompter: new Prompter({ input, output }) };
}

function fetchImpl(posts = []) {
  return async (url, init) => {
    const method = url.split('/').at(-1);
    const params = Object.fromEntries(new URLSearchParams(init.body));
    if (method === 'chat.postMessage') posts.push(params);
    const result = {
      'auth.test': { team_id: 'T0TEAM', team: 'Acme', user_id: 'U0BOT', bot_id: 'B0BOT' },
      'bots.info': { bot: { app_id: 'A0APP', name: 'vika' } },
      'apps.connections.open': { url: 'wss://wss-primary.slack.com/link/?ticket=1' },
      'chat.postMessage': { channel: params.channel, ts: '1.000001' },
    }[method];
    return Response.json({ ok: true, ...result });
  };
}

// Says hello as the app, then delivers the owner's direct message with the
// code once setup has shown it.
function sockets(text, appId = 'A0APP') {
  return class extends EventTarget {
    constructor() {
      super();
      const frame = (value) => this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(value) }));
      setImmediate(async () => {
        frame({ type: 'hello', num_connections: 1, connection_info: { app_id: appId } });
        const deadline = Date.now() + 3000;
        let code;
        while (!this.closed && !(code = text().match(/vika-[a-z2-9]{8}/)?.[0]) && Date.now() < deadline) await delay(5);
        frame({ type: 'events_api', envelope_id: 'e1', payload: { type: 'event_callback', team_id: 'T0TEAM', api_app_id: appId,
          event: { type: 'message', channel: 'D0OWNER', channel_type: 'im', user: 'U0OWNER', text: code,
            ts: `${Math.floor(Date.now() / 1000)}.000001` } } });
      });
    }
    send() {}
    close() { this.closed = true; this.dispatchEvent(new Event('close')); }
  };
}

test('the app manifest asks for Socket Mode, message events and a valid bot name, and never rotates tokens', () => {
  const manifest = appManifest('小V Agent');
  assert.equal(manifest.display_information.name, '小V Agent');
  assert.equal(manifest.features.bot_user.display_name, 'v-agent');
  assert.equal(appManifest('小').features.bot_user.display_name, 'vika');
  assert.equal(manifest.settings.socket_mode_enabled, true);
  assert.equal(manifest.settings.token_rotation_enabled, false);
  assert.deepEqual(manifest.settings.event_subscriptions.bot_events, ['message.channels', 'message.groups', 'message.im', 'message.mpim']);
  assert.ok(!manifest.oauth_config.scopes.bot.includes('chat:write.public'));
  for (const scope of ['channels:read', 'groups:read', 'im:read', 'mpim:read', 'users:read']) {
    assert.ok(manifest.oauth_config.scopes.bot.includes(scope), `member lookup needs ${scope}`);
  }
  assert.deepEqual(JSON.parse(new URL(createAppUrl('Vika')).searchParams.get('manifest_json')), appManifest('Vika'));
});

test('tokens are checked, hidden, and saved only after the owner sends the code', async (t) => {
  const f = fixture(t, ['xoxb-bad', BOT_TOKEN, APP_TOKEN]);
  const posts = [];
  await connectSlack({ plan: { dataDir: f.dataDir, displayName: 'Vika' }, prompter: f.prompter, fetchImpl: fetchImpl(posts),
    WebSocketImpl: sockets(f.text), receiver: { helloMs: 500, recheckMs: 10, backoffMs: [5] } });
  const state = new SlackState(f.dataDir);
  try {
    assert.deepEqual({ ...state.account(), activatedAt: 0 }, { teamId: 'T0TEAM', appId: 'A0APP', botId: 'B0BOT', botUserId: 'U0BOT',
      ownerId: 'U0OWNER', ownerChannel: 'D0OWNER', botToken: BOT_TOKEN, appToken: APP_TOKEN, activatedAt: 0 });
  } finally { state.close(); }
  const manifestFile = path.join(f.dataDir, 'slack', 'app-manifest.json');
  assert.deepEqual(JSON.parse(fs.readFileSync(manifestFile, 'utf8')), appManifest('Vika'));
  assert.equal(fs.statSync(manifestFile).mode & 0o777, 0o600);
  assert.match(f.text(), /https:\/\/api\.slack\.com\/apps\?new_app=1&manifest_json=/);
  assert.match(f.text(), /以 xoxb- 开头[\s\S]*私聊 vika，发送 vika-[a-z2-9]{8}[\s\S]*Slack 已接好（Acme）/);
  assert.ok(!f.text().includes(BOT_TOKEN) && !f.text().includes(APP_TOKEN));
  assert.equal(posts[0].channel, 'D0OWNER');
});

test('tokens from two different apps are refused and the old credentials stay', async (t) => {
  const f = fixture(t, [BOT_TOKEN, APP_TOKEN, '2']);
  const state = new SlackState(f.dataDir);
  state.saveAccount({ teamId: 'T0TEAM', appId: 'A0APP', botId: 'B0BOT', botUserId: 'U0BOT', ownerId: 'U0OLD', ownerChannel: 'D0OLD',
    botToken: 'xoxb-old-0000-abcdefghij', appToken: 'xapp-old-0000-abcdefghij', activatedAt: 1 });
  state.close();
  await assert.rejects(connectSlack({ plan: { dataDir: f.dataDir }, prompter: f.prompter, fetchImpl: fetchImpl(),
    WebSocketImpl: sockets(f.text, 'A0OTHER'), receiver: { helloMs: 500, recheckMs: 10, backoffMs: [5] } }), { name: 'PromptAborted' });
  assert.match(f.text(), /两个 token 不属于同一个 Slack App/);
  const after = new SlackState(f.dataDir);
  try {
    assert.equal(after.account().ownerId, 'U0OLD');
    after.acquire(); after.release();
  } finally { after.close(); }
});
