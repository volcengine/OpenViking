// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import * as posix from '../../daemon/src/platform/posix.mjs';
import * as windows from '../../daemon/src/platform/win32.mjs';
import { autostartCommand } from '../src/autostart.mjs';

function home(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-autostart-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return directory;
}

function run(homeDir, platform, action) {
  const calls = [];
  let output = '';
  return autostartCommand([action], {
    env: { PATH: '/opt/agents/bin:/usr/bin' }, homeDir, platform, uid: 501,
    execFileImpl: async (command, args) => { calls.push([command, ...args]); return { stdout: '' }; },
    output: { write: (text) => { output += text; } },
  }).then((code) => ({ code, calls, output }));
}

test('Linux: a systemd user unit that runs vika start with the enabling shell PATH', async (t) => {
  const directory = home(t);
  const enabled = await run(directory, { ...posix, loginService: 'systemd' }, 'enable');
  assert.equal(enabled.code, 0);
  const unit = fs.readFileSync(path.join(directory, '.config', 'systemd', 'user', 'vika.service'), 'utf8');
  assert.match(unit, new RegExp(`ExecStart=${directory}/\\.vika/bin/vika start`));
  assert.match(unit, /Environment=PATH=\/opt\/agents\/bin:\/usr\/bin/);
  assert.match(unit, new RegExp(`PIDFile=${directory}/\\.vika/pm2/pm2\\.pid`));
  assert.deepEqual(enabled.calls, [['systemctl', '--user', 'daemon-reload'], ['systemctl', '--user', 'enable', 'vika.service']]);
  assert.match(enabled.output, /enable-linger/);

  const disabled = await run(directory, { ...posix, loginService: 'systemd' }, 'disable');
  assert.equal(disabled.code, 0);
  assert.equal(fs.existsSync(path.join(directory, '.config', 'systemd', 'user', 'vika.service')), false);
});

test('macOS: a LaunchAgent bootstrapped into the user GUI domain', async (t) => {
  const directory = home(t);
  const enabled = await run(directory, { ...posix, loginService: 'launchd' }, 'enable');
  const plist = fs.readFileSync(path.join(directory, 'Library', 'LaunchAgents', 'com.vika.agent.plist'), 'utf8');
  assert.match(plist, /<string>start<\/string>/);
  assert.match(plist, /<key>RunAtLoad<\/key><true\/>/);
  assert.deepEqual(enabled.calls.at(-1), ['launchctl', 'bootstrap', 'gui/501', path.join(directory, 'Library', 'LaunchAgents', 'com.vika.agent.plist')]);
  assert.equal((await run(directory, { ...posix, loginService: 'launchd' }, 'bogus')).code, 2);
});

test('preview without a login service refuses autostart without writing or running anything', async t => {
  const directory = home(t);
  const result = await run(directory, windows, 'enable');
  assert.equal(result.code, 1);
  assert.match(result.output, /not supported/);
  assert.deepEqual(result.calls, []);
  assert.deepEqual(fs.readdirSync(directory), []);
});
