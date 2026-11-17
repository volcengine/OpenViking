// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import test from 'node:test';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { LarkCli } from '../src/lark-cli.mjs';

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

  const missing = spawnResult({ code: 1, stdout: '{"ok":false,"error":{"subtype":"not_configured"}}\n' });
  assert.equal(await new LarkCli('lark-cli', { spawnImpl: missing }).logoutUser({ profile: 'superagent-new' }), false);
});

test('logoutUser fails closed when lark-cli cannot revoke an existing login', async () => {
  const failed = spawnResult({ code: 1, stderr: 'token revoke failed' });
  await assert.rejects(new LarkCli('lark-cli', { spawnImpl: failed }).logoutUser({ profile: 'superagent-asd' }),
    /lark-cli auth logout failed: token revoke failed/);
});
