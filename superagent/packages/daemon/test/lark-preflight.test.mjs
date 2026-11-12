// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { it } from 'node:test';
import { LarkClient } from '../src/lark.mjs';

const quietLogger = { debug() {}, info() {}, warn() {}, error() {} };

it('requires joined-chat discovery during strict CLI preflight', async () => {
  const calls = [];
  const registry = {
    async run(_command, args) {
      calls.push(args);
      if (args.includes('event')) return { code: 0, stdout: '--event-types --quiet', stderr: '' };
      if (args.includes('im')) return { code: 0, stdout: '+messages-reply +messages-send', stderr: '' };
      return { code: 0, stdout: 'ok', stderr: '' };
    },
  };
  const client = new LarkClient({
    profile: 'test-profile',
    larkCliBin: '/fake/lark-cli',
    projectDir: '/tmp',
    sendTimeoutMs: 1_000,
    strictCliPreflight: true,
  }, registry, quietLogger);
  await assert.rejects(() => client.preflight(), /missing \+chat-list/);
  assert.ok(calls.some((args) => args.includes('im')));
});

it('reports the owner as the user the profile is logged in as, or none without a user login', async () => {
  const preflight = (status) => new LarkClient({ profile: 'test-profile', larkCliBin: '/fake/lark-cli', projectDir: '/tmp' }, {
    async run(_command, args) {
      if (args.includes('auth')) return status;
      if (args.includes('event')) return { code: 0, stdout: '--event-types --quiet', stderr: '' };
      if (args.includes('im')) return { code: 0, stdout: '+chat-list +messages-reply +messages-send', stderr: '' };
      return { code: 0, stdout: 'lark-cli 1.0.94', stderr: '' };
    },
  }, quietLogger).preflight();
  const loggedIn = { code: 0, stdout: JSON.stringify({ identities: { user: { openId: 'ou_owner12345678' } } }), stderr: '' };
  assert.deepEqual((await preflight(loggedIn)).owner, { id: 'ou_owner12345678', target: { user_id: 'ou_owner12345678' } });
  const botOnly = { code: 0, stdout: JSON.stringify({ identities: { user: { available: false } } }), stderr: '' };
  assert.equal((await preflight(botOnly)).owner, null);
  assert.equal((await preflight({ code: 1, stdout: '', stderr: 'boom' })).owner, null);
});

it('reports the bot itself from bot info, and runs on without it', async () => {
  const preflight = (info) => new LarkClient({ profile: 'test-profile', larkCliBin: '/fake/lark-cli', projectDir: '/tmp' }, {
    async run(_command, args) {
      if (args.includes('/open-apis/bot/v3/info')) {
        assert.deepEqual(args, ['--profile', 'test-profile', 'api', 'GET', '/open-apis/bot/v3/info', '--as', 'bot', '--json']);
        return info;
      }
      if (args.includes('event')) return { code: 0, stdout: '--event-types --quiet', stderr: '' };
      if (args.includes('im')) return { code: 0, stdout: '+chat-list +messages-reply +messages-send', stderr: '' };
      return { code: 0, stdout: 'lark-cli 1.0.94', stderr: '' };
    },
  }, quietLogger).preflight();
  // What lark-cli 1.0.94 (the pinned version) prints for this call.
  const real = { code: 0, stderr: '', stdout: JSON.stringify({
    ok: true,
    identity: 'bot',
    data: { activate_status: 2, app_name: 'TestBot', avatar_url: 'https://example.invalid/a.png', ip_white_list: [], open_id: 'ou_self12345678' },
    _notice: { update: { command: 'lark-cli update', current: '1.0.94', latest: '1.0.98' } },
  }) };
  assert.deepEqual((await preflight(real)).self, { id: 'ou_self12345678' });
  for (const broken of [
    { code: 1, stdout: '', stderr: 'permission denied' },
    { code: 0, stdout: 'not json', stderr: '' },
    { code: 0, stdout: JSON.stringify({ ok: true, data: { app_name: 'TestBot' } }), stderr: '' },
    { code: 0, stdout: JSON.stringify({ ok: true, data: { open_id: 'cli_notauser' } }), stderr: '' },
  ]) {
    assert.equal((await preflight(broken)).self, null);
  }
});
