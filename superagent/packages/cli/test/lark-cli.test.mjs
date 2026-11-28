// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import test from 'node:test';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { LarkCli, larkCliEnv } from '../src/lark-cli.mjs';

test('larkCliEnv strips a host agent workspace lark-cli refuses to run in', () => {
  const env = larkCliEnv({
    PATH: '/bin',
    HERMES_HOME: '/home/me/.hermes',
    OPENCLAW_HOME: '/home/me/.openclaw',
    CODEX_HOME: '/home/me/.codex',
    LARKSUITE_CLI_PROFILE: 'inherited',
  });
  assert.equal(env.HERMES_HOME, undefined);
  assert.equal(env.OPENCLAW_HOME, undefined);
  assert.equal(env.LARKSUITE_CLI_PROFILE, undefined);
  assert.equal(env.CODEX_HOME, '/home/me/.codex');
  assert.equal(env.PATH, '/bin');
  assert.equal(env.LARKSUITE_CLI_NO_UPDATE_NOTIFIER, '1');
});

function spawnResult({ code = 0, stdout = '', stderr = '' } = {}) {
  return (_bin, args) => {
    const child = new EventEmitter();
    child.args = args;
    child.stdin = new PassThrough();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.kill = () => {};
    queueMicrotask(() => {
      if (stdout) child.stdout.write(stdout);
      if (stderr) child.stderr.write(stderr);
      child.stdout.end();
      child.stderr.end();
      child.emit('close', code);
    });
    spawnResult.last = child;
    return child;
  };
}

test('logoutUser clears the selected profile and ignores a profile that does not exist yet', async () => {
  const ok = spawnResult({ stdout: '{"ok":true}\n' });
  const larkCli = new LarkCli('lark-cli', { spawnImpl: ok });
  assert.equal(await larkCli.logoutUser({ profile: 'superagent-asd' }), true);
  assert.deepEqual(spawnResult.last.args, ['--profile', 'superagent-asd', 'auth', 'logout', '--json']);

  // As lark-cli 1.0.99 reports it: exit 3, the error on stderr, stdout empty.
  const missing = spawnResult({ code: 3, stderr: '{\n  "ok": false,\n  "error": {\n    "type": "config",\n    "subtype": "not_configured",\n'
    + '    "message": "profile \\"superagent-new\\" not found"\n  }\n}\n' });
  assert.equal(await new LarkCli('lark-cli', { spawnImpl: missing }).logoutUser({ profile: 'superagent-new' }), false);
});

test('logoutUser fails closed when lark-cli cannot revoke an existing login', async () => {
  const failed = spawnResult({ code: 1, stderr: 'token revoke failed' });
  await assert.rejects(new LarkCli('lark-cli', { spawnImpl: failed }).logoutUser({ profile: 'superagent-asd' }),
    /lark-cli auth logout failed: token revoke failed/);
});
