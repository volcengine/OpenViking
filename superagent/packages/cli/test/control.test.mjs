// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { appsFor, pm2StopTimeout, restartCommand, startCommand, statusCommand, stopCommand } from '../src/control.mjs';

const windows = process.platform === 'win32';
const TARGETS = JSON.stringify({
  alpha: { app: 'superagent-alpha', readyUrl: 'http://127.0.0.1:3738/ready', upgradeSharedWeb: false },
  beta: { app: 'superagent-beta', readyUrl: 'http://127.0.0.1:3739/ready', upgradeSharedWeb: false },
});

// ~/.ov-superagent with one installed release and a fake pm2 that tracks app state.
function fixture(t, online = []) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-control-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const release = path.join(home, '.ov-superagent', 'releases', '0.4.0-test');
  fs.mkdirSync(path.join(release, 'packages', 'daemon', 'src'), { recursive: true });
  fs.writeFileSync(path.join(release, 'ecosystem.config.cjs'), 'module.exports = {};\n');
  const script = path.join(release, 'packages', 'daemon', 'src', 'main.mjs');
  fs.writeFileSync(script, '');
  const releaseLink = path.join(home, '.ov-superagent', 'current');
  fs.symlinkSync(release, releaseLink, windows ? 'junction' : 'dir');
  const apps = new Map(online.map((name) => [name, 'online']));
  const commands = [];
  const timeouts = [];
  const execFileImpl = async (bin, args, execOptions = {}) => {
    commands.push(args.join(' '));
    timeouts.push([args[0], execOptions.timeout]);
    const [verb] = args;
    if (verb === 'jlist') {
      return { stdout: JSON.stringify([...apps].map(([name, status]) => ({ name, pm2_env: { status, pm_exec_path: script, kill_timeout: 30000 } }))) };
    }
    if (verb === 'start') apps.set(args[3], 'online');
    if (verb === 'stop') apps.set(args[1], 'stopped');
    if (verb === 'delete') apps.delete(args[1]);
    return { stdout: '' };
  };
  const ready = [];
  const fetchImpl = async (url) => { ready.push(url); return { ok: true }; };
  let output = '';
  let errors = '';
  const options = {
    env: { OV_SUPERAGENT_UPDATER_TARGETS_JSON: TARGETS, HOME: home, USERPROFILE: home }, releaseLink, execFileImpl, fetchImpl,
    pm2Bin: 'pm2', socketPath: path.join(home, '.ov-superagent', 'updater.sock'), readyOptions: { attempts: 1, intervalMs: 1 },
    stdout: { write: (text) => { output += text; } },
    stderr: { write: (text) => { errors += text; } },
  };
  return { options, commands, timeouts, ready, apps, output: () => output, stderr: () => errors };
}

test('a target names one instance, the Web, the updater, or everything', () => {
  const targets = JSON.parse(TARGETS);
  assert.deepEqual(appsFor('beta', targets).map(({ app }) => app), ['superagent-beta']);
  assert.deepEqual(appsFor(undefined, targets).map(({ app }) => app), ['superagent-alpha', 'superagent-beta', 'superagent-web', ...(windows ? [] : ['superagent-updater'])]);
  assert.deepEqual(appsFor('web', targets).map(({ app }) => app), ['superagent-web']);
  assert.throws(() => appsFor('gamma', targets), /unknown instance gamma/);
});

test('start registers every app, then reports readiness; online apps are left alone', async (t) => {
  const f = fixture(t, ['superagent-alpha']);
  const result = await startCommand(undefined, { ...f.options, socketPath: '/nonexistent.sock' }).catch((error) => error.message);
  // The updater never answers on a nonexistent socket; every other app did.
  assert.equal(result, windows ? 0 : 1);
  assert.deepEqual(f.commands.filter((command) => command.startsWith('start')), [
    `start ${fs.realpathSync(path.join(path.dirname(f.options.releaseLink), 'releases', '0.4.0-test', 'ecosystem.config.cjs'))} --only superagent-beta --update-env`,
    `start ${fs.realpathSync(path.join(path.dirname(f.options.releaseLink), 'releases', '0.4.0-test', 'ecosystem.config.cjs'))} --only superagent-web --update-env`,
    ...(windows ? [] : [`start ${fs.realpathSync(path.join(path.dirname(f.options.releaseLink), 'releases', '0.4.0-test', 'ecosystem.config.cjs'))} --only superagent-updater --update-env`]),
  ]);
  assert.match(f.output(), /superagent-alpha 已就绪/);
  assert.deepEqual(f.ready, ['http://127.0.0.1:3738/ready', 'http://127.0.0.1:3739/ready', 'http://127.0.0.1:3737/ready']);
  if (windows) assert.equal(f.stderr(), '');
  else assert.match(f.stderr(), /superagent-updater: updater did not become ready/);
});

test('start reports an online daemon that never becomes ready, without touching it', async (t) => {
  const f = fixture(t, ['superagent-alpha']);
  const code = await startCommand('alpha', {
    ...f.options, fetchImpl: async () => ({ ok: false, status: 503 }),
  });
  assert.equal(code, 1);
  assert.match(f.stderr(), /superagent-alpha: daemon did not become ready.*503/);
  assert.doesNotMatch(f.output(), /superagent-alpha 已就绪/);
  assert.ok(!f.commands.some((command) => command.startsWith('delete') || command.startsWith('stop')),
    'must not restart/delete an online app');
});

test('start verifies the identity of an already online app before reporting it', async (t) => {
  const f = fixture(t, ['superagent-alpha']);
  const outside = path.join(path.dirname(f.options.releaseLink), 'unrelated.mjs');
  fs.writeFileSync(outside, '');
  const code = await startCommand('alpha', {
    ...f.options, execFileImpl: async () => ({ stdout: JSON.stringify([{
      name: 'superagent-alpha', pm2_env: { status: 'online', pm_exec_path: outside },
    }]) }),
  });
  assert.equal(code, 1);
  assert.match(f.stderr(), /outside the current release/);
  assert.deepEqual(f.ready, []);
});

test('start checks an already online updater socket instead of announcing success', async (t) => {
  const f = fixture(t, ['superagent-updater']);
  await assert.rejects(startCommand('updater', f.options), windows ? /do not support the release updater/ : /updater did not become ready/);
  assert.doesNotMatch(f.output(), /已经在运行/);
});

test('restart replaces a running app; stop leaves a stopped app; both save the pm2 list', async (t) => {
  const f = fixture(t, ['superagent-alpha']);
  await restartCommand('alpha', f.options);
  assert.deepEqual(f.commands.filter((command) => !command.startsWith('jlist')).map((command) => command.split(' ')[0]),
    ['delete', 'start', 'save']);
  await stopCommand('alpha', f.options);
  assert.equal(f.apps.get('superagent-alpha'), 'stopped');
  assert.equal(f.commands.at(-1), 'save');
});

test('delete and stop wait longer than the app kill_timeout', async (t) => {
  assert.equal(pm2StopTimeout({ pm2_env: { kill_timeout: 30000 } }), 60_000);
  assert.equal(pm2StopTimeout({ pm2_env: { kill_timeout: 5000 } }), 35_000);
  assert.equal(pm2StopTimeout(undefined), 60_000);
  const f = fixture(t, ['superagent-alpha']);
  await restartCommand('alpha', f.options);
  await stopCommand('alpha', f.options);
  for (const verb of ['delete', 'stop']) {
    const [, timeout] = f.timeouts.find(([v]) => v === verb);
    assert.ok(timeout > 30000, `${verb} timeout ${timeout} must exceed kill_timeout`);
  }
});

test('start explains when no release is installed', async (t) => {
  const f = fixture(t);
  if (windows) fs.rmdirSync(f.options.releaseLink);
  else fs.unlinkSync(f.options.releaseLink);
  await assert.rejects(startCommand('alpha', f.options), /no OpenViking SuperAgent release is installed/);
});

test('start reports readiness from the ready endpoint instead of polling PM2', async (t) => {
  const f = fixture(t);
  let jlistCalls = 0;
  const base = f.options.execFileImpl;
  f.options.execFileImpl = async (bin, args, options) => {
    if (args[0] === 'jlist') jlistCalls += 1;
    return base(bin, args, options);
  };
  const code = await startCommand('alpha', { ...f.options, readyOptions: { attempts: 2, intervalMs: 1 } });
  assert.equal(code, 0);
  assert.equal(f.apps.get('superagent-alpha'), 'online');
  assert.equal(f.commands.filter((command) => command.startsWith('start')).length, 1, 'started once');
  assert.equal(jlistCalls, 1, 'only the initial list; readiness comes from the endpoint');
  assert.match(f.output(), /superagent-alpha 已就绪/);
});

test('restart does not retry a terminal PM2 state', async (t) => {
  const f = fixture(t);
  const base = f.options.execFileImpl;
  let jlistCalls = 0;
  f.options.execFileImpl = async (bin, args, options) => {
    if (args[0] === 'jlist' && f.apps.has('superagent-alpha')) {
      jlistCalls += 1;
      return { stdout: JSON.stringify([{
        name: 'superagent-alpha',
        pm2_env: { status: 'errored', exit_code: 1, unstable_restarts: 2, pm_exec_path: path.join(fs.realpathSync(f.options.releaseLink), 'packages', 'daemon', 'src', 'main.mjs') },
      }]) };
    }
    return base(bin, args, options);
  };
  await assert.rejects(
    restartCommand('alpha', { ...f.options, readyOptions: { attempts: 40, intervalMs: 1, onlineTimeoutMs: 100 } }),
    /status=errored, exit_code=1, unstable_restarts=2/,
  );
  assert.equal(jlistCalls, 1);
});

test('restart gives each PM2 probe no more than the remaining online deadline', async (t) => {
  const f = fixture(t);
  const base = f.options.execFileImpl;
  const timeouts = [];
  f.options.execFileImpl = async (bin, args, options) => {
    if (args[0] === 'jlist' && f.apps.has('superagent-alpha')) {
      timeouts.push(options.timeout);
      return { stdout: JSON.stringify([{
        name: 'superagent-alpha',
        pm2_env: { status: 'one-launch-status', pm_exec_path: path.join(fs.realpathSync(f.options.releaseLink), 'packages', 'daemon', 'src', 'main.mjs') },
      }]) };
    }
    return base(bin, args, options);
  };
  await assert.rejects(
    restartCommand('alpha', { ...f.options, readyOptions: { attempts: 40, intervalMs: 1, onlineTimeoutMs: 5 } }),
    /status=one-launch-status/,
  );
  assert.ok(timeouts.length >= 1);
  assert.ok(timeouts.every((timeout) => timeout > 0 && timeout <= 5));
});

test('a failed instance does not keep the shared Web out of PM2 or out of the dump', async (t) => {
  const f = fixture(t);
  // The instance's daemon never answers /ready, like a DSH start that timed out.
  const fetchImpl = async (url) => (url.includes(':3738/') ? { ok: false, status: 503 } : { ok: true });
  const code = await startCommand(undefined, {
    ...f.options, fetchImpl, readyOptions: { attempts: 2, intervalMs: 1, onlineTimeoutMs: 100 },
  });
  assert.equal(code, 1);
  // Both the failed instance and the Web are registered, and the Web runs.
  assert.equal(f.apps.get('superagent-alpha'), 'online');
  assert.equal(f.apps.get('superagent-web'), 'online');
  // The dump is written after the Web start, so a resurrect brings it back.
  const webStart = f.commands.findIndex((command) => command.includes('--only superagent-web'));
  const save = f.commands.indexOf('save');
  assert.ok(webStart >= 0, 'the Web is started');
  assert.ok(save > webStart, 'the Web start precedes the save');
  // The failure is reported by name with its reason; the Web is not stopped or deleted.
  assert.match(f.stderr(), /superagent-alpha: daemon did not become ready.*503/);
  assert.ok(!f.commands.some((command) => command.startsWith('delete') || command.startsWith('stop')));
  assert.match(f.output(), /superagent-beta 已就绪/);
  assert.match(f.output(), /superagent-web 已就绪/);
});

test('status lists every app with its pm2 state and readiness', async (t) => {
  const f = fixture(t, ['superagent-alpha']);
  await statusCommand(f.options);
  assert.match(f.output(), /superagent-alpha\s+运行中\s+就绪/);
  assert.match(f.output(), /superagent-beta\s+未启动\s+-/);
  if (windows) assert.doesNotMatch(f.output(), /superagent-updater/);
  else assert.match(f.output(), /superagent-updater\s+未启动/);
});

test('status names the manifest settings this version ignores', async (t) => {
  const f = fixture(t);
  const home = f.options.env.HOME;
  fs.writeFileSync(path.join(home, '.ov-superagent', 'config.json'), JSON.stringify({
    version: 1,
    defaultInstance: 'alpha',
    env: { OV_SUPERAGENT_COGNITION_MAX_CHARS: '120000' },
    agents: [{
      id: 'alpha', displayName: 'Alpha', profile: 'superagent-alpha', port: 3738, model: 'codex',
      dataDir: path.join(home, 'data'), workspace: path.join(home, 'workspace'), ovConfig: path.join(home, 'ov.json'),
    }],
  }));
  await statusCommand(f.options);
  assert.match(f.output(), /superagent-alpha\s+未启动/);
  assert.match(f.output(), /已忽略.*config\.json 删掉：env\.OV_SUPERAGENT_COGNITION_MAX_CHARS/);
});

