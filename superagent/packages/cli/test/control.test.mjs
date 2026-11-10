// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { appsFor, restartCommand, startCommand, statusCommand, stopCommand } from '../src/control.mjs';

const windows = process.platform === 'win32';
const TARGETS = JSON.stringify({
  alpha: { app: 'vika-alpha', readyUrl: 'http://127.0.0.1:3738/ready', upgradeSharedWeb: false },
  beta: { app: 'vika-beta', readyUrl: 'http://127.0.0.1:3739/ready', upgradeSharedWeb: false },
});

// ~/.ovsa with one installed release and a fake pm2 that tracks app state.
function fixture(t, online = []) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-control-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const release = path.join(home, '.ovsa', 'releases', '0.4.0-test');
  fs.mkdirSync(path.join(release, 'packages', 'daemon', 'src'), { recursive: true });
  fs.writeFileSync(path.join(release, 'ecosystem.config.cjs'), 'module.exports = {};\n');
  const script = path.join(release, 'packages', 'daemon', 'src', 'main.mjs');
  fs.writeFileSync(script, '');
  const releaseLink = path.join(home, '.ovsa', 'current');
  fs.symlinkSync(release, releaseLink, windows ? 'junction' : 'dir');
  const apps = new Map(online.map((name) => [name, 'online']));
  const commands = [];
  const execFileImpl = async (bin, args) => {
    commands.push(args.join(' '));
    const [verb] = args;
    if (verb === 'jlist') {
      return { stdout: JSON.stringify([...apps].map(([name, status]) => ({ name, pm2_env: { status, pm_exec_path: script } }))) };
    }
    if (verb === 'start') apps.set(args[3], 'online');
    if (verb === 'stop') apps.set(args[1], 'stopped');
    if (verb === 'delete') apps.delete(args[1]);
    return { stdout: '' };
  };
  const ready = [];
  const fetchImpl = async (url) => { ready.push(url); return { ok: true }; };
  let output = '';
  const options = {
    env: { VIKA_UPDATER_TARGETS_JSON: TARGETS, HOME: home, USERPROFILE: home }, releaseLink, execFileImpl, fetchImpl,
    pm2Bin: 'pm2', socketPath: path.join(home, '.ovsa', 'updater.sock'), readyOptions: { attempts: 1, intervalMs: 1 },
    stdout: { write: (text) => { output += text; } },
  };
  return { options, commands, ready, apps, output: () => output };
}

test('a target names one instance, the Web, the updater, or everything', () => {
  const targets = JSON.parse(TARGETS);
  assert.deepEqual(appsFor('beta', targets).map(({ app }) => app), ['vika-beta']);
  assert.deepEqual(appsFor(undefined, targets).map(({ app }) => app), ['vika-alpha', 'vika-beta', 'vika-web', ...(windows ? [] : ['vika-updater'])]);
  assert.deepEqual(appsFor('web', targets).map(({ app }) => app), ['vika-web']);
  assert.throws(() => appsFor('gamma', targets), /unknown instance gamma/);
});

test('start runs the installed release and leaves online apps alone; nothing is fetched or published', async (t) => {
  const f = fixture(t, ['vika-alpha']);
  const result = await startCommand(undefined, { ...f.options, socketPath: '/nonexistent.sock' }).catch((error) => error.message);
  if (windows) assert.equal(result, 0);
  else assert.equal(result, 'updater did not become ready on /nonexistent.sock');
  assert.deepEqual(f.commands.filter((command) => command.startsWith('start')), [
    `start ${fs.realpathSync(path.join(path.dirname(f.options.releaseLink), 'releases', '0.4.0-test', 'ecosystem.config.cjs'))} --only vika-beta --update-env`,
    `start ${fs.realpathSync(path.join(path.dirname(f.options.releaseLink), 'releases', '0.4.0-test', 'ecosystem.config.cjs'))} --only vika-web --update-env`,
    ...(windows ? [] : [`start ${fs.realpathSync(path.join(path.dirname(f.options.releaseLink), 'releases', '0.4.0-test', 'ecosystem.config.cjs'))} --only vika-updater --update-env`]),
  ]);
  assert.match(f.output(), /vika-alpha 已经在运行/);
  assert.deepEqual(f.ready, ['http://127.0.0.1:3738/ready', 'http://127.0.0.1:3739/ready', 'http://127.0.0.1:3737/ready']);
});

test('start refuses to report an online daemon as ready when its runtime has not started', async (t) => {
  const f = fixture(t, ['vika-alpha']);
  await assert.rejects(startCommand('alpha', {
    ...f.options, fetchImpl: async () => ({ ok: false, status: 503 }),
  }), /daemon did not become ready.*503/);
  assert.doesNotMatch(f.output(), /已经在运行|已就绪/);
  assert.ok(f.commands.every((command) => command === 'jlist'), 'must not restart/delete an online app');
});

test('start verifies the identity of an already online app before checking ready', async (t) => {
  const f = fixture(t, ['vika-alpha']);
  const outside = path.join(path.dirname(f.options.releaseLink), 'unrelated.mjs');
  fs.writeFileSync(outside, '');
  await assert.rejects(startCommand('alpha', {
    ...f.options, execFileImpl: async () => ({ stdout: JSON.stringify([{
      name: 'vika-alpha', pm2_env: { status: 'online', pm_exec_path: outside },
    }]) }),
  }), /outside the current release/);
  assert.deepEqual(f.ready, []);
});

test('start checks an already online updater socket instead of announcing success', async (t) => {
  const f = fixture(t, ['vika-updater']);
  await assert.rejects(startCommand('updater', f.options), windows ? /do not support the release updater/ : /updater did not become ready/);
  assert.doesNotMatch(f.output(), /已经在运行/);
});

test('restart replaces a running app; stop leaves a stopped app; both save the pm2 list', async (t) => {
  const f = fixture(t, ['vika-alpha']);
  await restartCommand('alpha', f.options);
  assert.deepEqual(f.commands.filter((command) => !command.startsWith('jlist')).map((command) => command.split(' ')[0]),
    ['delete', 'start', 'save']);
  await stopCommand('alpha', f.options);
  assert.equal(f.apps.get('vika-alpha'), 'stopped');
  assert.equal(f.commands.at(-1), 'save');
});

test('start explains when no release is installed', async (t) => {
  const f = fixture(t);
  if (windows) fs.rmdirSync(f.options.releaseLink);
  else fs.unlinkSync(f.options.releaseLink);
  await assert.rejects(startCommand('alpha', f.options), /no OpenViking SuperAgent release is installed/);
});

test('start tolerates PM2 launching before the process becomes online', async (t) => {
  const f = fixture(t);
  let launching = true;
  const base = f.options.execFileImpl;
  f.options.execFileImpl = async (bin, args) => {
    if (args[0] === 'jlist' && f.apps.has('vika-alpha') && launching) {
      launching = false;
      return { stdout: JSON.stringify([{
        name: 'vika-alpha',
        pm2_env: { status: 'one-launch-status', pm_exec_path: path.join(fs.realpathSync(f.options.releaseLink), 'packages', 'daemon', 'src', 'main.mjs') },
      }]) };
    }
    return base(bin, args);
  };
  await startCommand('alpha', { ...f.options, readyOptions: { attempts: 2, intervalMs: 1 } });
  assert.equal(f.apps.get('vika-alpha'), 'online');
});

test('start does not retry a terminal PM2 state', async (t) => {
  const f = fixture(t);
  const base = f.options.execFileImpl;
  let jlistCalls = 0;
  f.options.execFileImpl = async (bin, args, options) => {
    if (args[0] === 'jlist' && f.apps.has('vika-alpha')) {
      jlistCalls += 1;
      return { stdout: JSON.stringify([{
        name: 'vika-alpha',
        pm2_env: { status: 'errored', exit_code: 1, unstable_restarts: 2, pm_exec_path: path.join(fs.realpathSync(f.options.releaseLink), 'packages', 'daemon', 'src', 'main.mjs') },
      }]) };
    }
    return base(bin, args, options);
  };
  await assert.rejects(
    startCommand('alpha', { ...f.options, readyOptions: { attempts: 40, intervalMs: 1, onlineTimeoutMs: 100 } }),
    /status=errored, exit_code=1, unstable_restarts=2/,
  );
  assert.equal(jlistCalls, 1);
});

test('start gives each PM2 probe no more than the remaining online deadline', async (t) => {
  const f = fixture(t);
  const base = f.options.execFileImpl;
  const timeouts = [];
  f.options.execFileImpl = async (bin, args, options) => {
    if (args[0] === 'jlist' && f.apps.has('vika-alpha')) {
      timeouts.push(options.timeout);
      return { stdout: JSON.stringify([{
        name: 'vika-alpha',
        pm2_env: { status: 'one-launch-status', pm_exec_path: path.join(fs.realpathSync(f.options.releaseLink), 'packages', 'daemon', 'src', 'main.mjs') },
      }]) };
    }
    return base(bin, args, options);
  };
  await assert.rejects(
    startCommand('alpha', { ...f.options, readyOptions: { attempts: 40, intervalMs: 1, onlineTimeoutMs: 5 } }),
    /status=one-launch-status/,
  );
  assert.ok(timeouts.length >= 1);
  assert.ok(timeouts.every((timeout) => timeout > 0 && timeout <= 5));
});

test('status lists every app with its pm2 state and readiness', async (t) => {
  const f = fixture(t, ['vika-alpha']);
  await statusCommand(f.options);
  assert.match(f.output(), /vika-alpha\s+运行中\s+就绪/);
  assert.match(f.output(), /vika-beta\s+未启动\s+-/);
  if (windows) assert.doesNotMatch(f.output(), /vika-updater/);
  else assert.match(f.output(), /vika-updater\s+未启动/);
});

test('status names the manifest settings this version ignores', async (t) => {
  const f = fixture(t);
  const home = f.options.env.HOME;
  fs.writeFileSync(path.join(home, '.ovsa', 'config.json'), JSON.stringify({
    version: 1,
    defaultInstance: 'alpha',
    env: { VIKA_COGNITION_MAX_CHARS: '120000' },
    agents: [{
      id: 'alpha', displayName: 'Alpha', profile: 'vika-alpha', port: 3738, model: 'codex',
      dataDir: path.join(home, 'data'), workspace: path.join(home, 'workspace'), ovConfig: path.join(home, 'ov.json'),
    }],
  }));
  await statusCommand(f.options);
  assert.match(f.output(), /vika-alpha\s+未启动/);
  assert.match(f.output(), /已忽略.*config\.json 删掉：env\.VIKA_COGNITION_MAX_CHARS/);
});
