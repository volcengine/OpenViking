// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import { spawnSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import * as posix from '../../daemon/src/platform/posix.mjs';
import * as windows from '../../daemon/src/platform/win32.mjs';
import { autostartCommand, autostartEnabled, startupEntry } from '../src/autostart.mjs';

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

test('Windows: a Startup-folder script that starts vika hidden, with no service and no elevation', async (t) => {
  const directory = home(t);
  const launcher = path.join(directory, '.vika', 'bin', 'vika.cmd');
  fs.mkdirSync(path.dirname(launcher), { recursive: true });
  fs.writeFileSync(launcher, '@echo off\r\n');
  const entry = path.join(directory, 'AppData', 'Roaming', 'Microsoft', 'Windows',
    'Start Menu', 'Programs', 'Startup', 'vika-autostart.vbs');

  const enabled = await run(directory, windows, 'enable');
  assert.equal(enabled.code, 0);
  assert.match(enabled.output, /vika-autostart\.vbs/);
  // No subprocess: writing one file is the whole mechanism.
  assert.deepEqual(enabled.calls, []);
  assert.equal(autostartEnabled({ homeDir: directory, platform: windows, env: {} }), true);

  // WSH reads anything but UTF-16LE as ANSI, and a profile path need not be ASCII.
  const bytes = fs.readFileSync(entry);
  assert.deepEqual([...bytes.subarray(0, 2)], [0xff, 0xfe], 'the entry starts with a UTF-16LE BOM');
  const text = bytes.subarray(2).toString('utf16le');
  assert.equal(text, startupEntry(directory, '/opt/agents/bin:/usr/bin'));
  assert.ok(text.includes(launcher), 'it runs the installed launcher');
  assert.ok(text.includes(path.join(directory, '.vika', 'logs', 'autostart.log')), 'output goes to the autostart log');
  assert.ok(text.includes('shell.Run command, 0, False'), 'the window is hidden and the script does not block');
  assert.ok(text.includes('mkdir'), 'the log directory is recreated before the redirect');
  // The redirect needs the directory to exist before the first logon.
  assert.equal(fs.existsSync(path.join(directory, '.vika', 'logs')), true);

  const disabled = await run(directory, windows, 'disable');
  assert.equal(disabled.code, 0);
  assert.deepEqual(disabled.calls, []);
  assert.equal(fs.existsSync(entry), false);
  assert.equal(autostartEnabled({ homeDir: directory, platform: windows, env: {} }), false);
});

test('Windows: enabling before the release is installed says what is missing', async (t) => {
  const directory = home(t);
  const result = await run(directory, windows, 'enable');
  assert.equal(result.code, 1);
  assert.match(result.output, /vika\.cmd/, 'the message names the launcher it wants');
  assert.deepEqual(result.calls, []);
  assert.equal(fs.existsSync(path.join(directory, 'AppData')), false, 'nothing is written');
});

test('a platform with no login mechanism refuses autostart without writing or running anything', async t => {
  const directory = home(t);
  const result = await run(directory, { ...windows, loginService: null }, 'enable');
  assert.equal(result.code, 1);
  assert.deepEqual(result.calls, []);
  assert.deepEqual(fs.readdirSync(directory), []);
});

// Exercise the default argument resolution, rather than injecting homeDir.
test('Windows: HOME override selects launcher, USERPROFILE/APPDATA selects Startup folder', async t => {
  const directory = home(t);
  const custom = path.join(directory, 'custom home');
  const profile = path.join(directory, 'windows-profile');
  const roaming = path.join(profile, 'AppData', 'Roaming');
  const launcher = path.join(custom, '.vika', 'bin', 'vika.cmd');
  fs.mkdirSync(path.dirname(launcher), { recursive: true });
  fs.writeFileSync(launcher, '@echo off\r\n');
  const env = { HOME: custom, USERPROFILE: profile, APPDATA: roaming, PATH: 'C:\\tools;C:\\Windows' };
  let output = '';
  assert.equal(await autostartCommand(['enable'], { env, platform: windows, output: { write: s => { output += s; } } }), 0, output);
  const entry = path.join(roaming, 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup', 'vika-autostart.vbs');
  const text = fs.readFileSync(entry).subarray(2).toString('utf16le');
  assert.ok(text.includes(launcher));
  assert.ok(text.includes(`shell.Environment("PROCESS")("HOME") = "${custom}"`));
  assert.ok(text.includes(env.PATH));
  assert.equal(autostartEnabled({ env, platform: windows }), true);
  assert.equal(fs.existsSync(path.join(profile, '.vika')), false);
  assert.equal(await autostartCommand(['disable'], { env, platform: windows, output: { write() {} } }), 0);
  assert.equal(fs.existsSync(entry), false);
});

// Execute the generated VBScript, including cmd parsing, without starting Vika.
test('Windows: Startup executes the launcher with existing or missing logs', { skip: process.platform !== 'win32' }, t => {
  const directory = path.join(home(t), '中文 home');
  const bin = path.join(directory, '.vika', 'bin');
  const logs = path.join(directory, '.vika', 'logs');
  fs.mkdirSync(bin, { recursive: true });
  fs.writeFileSync(path.join(bin, 'vika.cmd'), '@echo off\r\necho VIKA-START-%1\r\n');
  const script = path.join(directory, 'startup-test.vbs');
  // Wait only in the test so completion is deterministic; production is hidden/async.
  const content = startupEntry(directory, process.env.PATH).replace('shell.Run command, 0, False', 'WScript.Quit shell.Run(command, 0, True)');
  fs.writeFileSync(script, Buffer.concat([Buffer.from([255, 254]), Buffer.from(content, 'utf16le')]));
  for (const exists of [true, false]) {
    fs.rmSync(logs, { recursive: true, force: true });
    if (exists) fs.mkdirSync(logs, { recursive: true });
    const result = spawnSync(path.join(process.env.SystemRoot, 'System32', 'cscript.exe'), ['//nologo', script], { encoding: 'utf8', timeout: 15000 });
    assert.equal(result.status, 0, result.stderr || result.error?.message);
    assert.match(fs.readFileSync(path.join(logs, 'autostart.log'), 'utf8'), /VIKA-START-start/);
  }
});
