// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import * as native from 'node:child_process';
import * as posix from '../src/platform/posix.mjs';
import * as selected from '../src/platform/index.mjs';

const options = { skip: process.platform === 'win32' };
test('POSIX process APIs are the native functions, including their overloads and custom promisifier', options, () => {
  assert.equal(posix.spawn, native.spawn);
  assert.equal(posix.execFile, native.execFile);
  assert.equal(selected.spawn, native.spawn);
  assert.equal(selected.execFile, native.execFile);
});
test('POSIX environment and configuration retain upstream semantics', options, () => {
  const env = { HOME: '/home/test', USERPROFILE: '/ignored', Path: '/ignored', PATH: '/bin', TEMP: '/ignored' };
  assert.equal(posix.normalizeEnvironment(env), env);
  assert.equal(posix.homeDir(env), env.HOME);
  assert.equal(posix.homeDir({ USERPROFILE: '/not-home' }), undefined);
  assert.equal(posix.localSocketPath('/tmp/test'), path.join('/tmp/test', 'actions.sock'));
  assert.equal(posix.binName('lark-cli'), 'lark-cli');
  assert.equal(posix.isNamedPipe('anything'), false);
  assert.equal(posix.spawnDetached, true);
  assert.deepEqual(posix.processOptions, {});
  const listeners = process.listenerCount('message');
  posix.onShutdown(() => assert.fail('no POSIX message handler'));
  assert.equal(process.listenerCount('message'), listeners);
  const args = ['a b'];
  assert.deepEqual(posix.executableInvocation('tool', args), { command: 'tool', args });
  assert.equal(posix.executableInvocation('tool', args).args, args);
  for (const value of ['~', '~/work', '~\\work', 'relative']) {
    const home = '/home/$&';
    assert.equal(posix.expandHome(value, home), value === '~' ? home : value.replace(/^~(?=\/)/, home));
  }
});
test('POSIX executable lookup, private directory and links use native filesystem behavior', options, t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-posix-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const executable = path.join(root, 'tool');
  fs.writeFileSync(executable, 'fixture', { mode: 0o600 });
  assert.equal(posix.findExecutable('tool', { PATH: root }), null);
  fs.chmodSync(executable, 0o700);
  assert.equal(posix.findExecutable('tool', { PATH: root }), executable);
  assert.equal(posix.findExecutable('missing', { PATH: root }), null);
  const directory = path.join(root, 'private');
  assert.equal(posix.preparePrivateDirectory(directory), directory);
  assert.equal(fs.statSync(directory).mode & 0o777, 0o700);
  assert.doesNotThrow(() => posix.assertPrivateDirectory(directory));
  fs.chmodSync(directory, 0o755);
  assert.throws(() => posix.assertPrivateDirectory(directory), /0700/);
  const link = path.join(root, 'link');
  posix.linkConfig(executable, link);
  assert.equal(fs.readlinkSync(link), executable);
  assert.doesNotThrow(() => posix.linkConfig(executable, link));
});
// Real process-group termination, including grandchildren after leader exit,
// remains covered by process-utils.test.mjs rather than a mocked kill assertion.
