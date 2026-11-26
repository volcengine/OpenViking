// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and the OpenViking contributors.
// SPDX-License-Identifier: AGPL-3.0
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFile, execFileSync, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { autostartCommand, autostartEnabled } from '../src/autostart.mjs';
import { pm2Defaults } from '../src/control.mjs';
import { runitScript, serviceDirectory } from '../src/termux-autostart.mjs';
import * as termux from '../../daemon/src/platform/termux.mjs';
import { allowedPm2Environment } from '../../updater/src/pm2-platform.mjs';

const sourceRoot = fileURLToPath(new URL('../../../', import.meta.url));
const quote = value => `'${String(value).replaceAll("'", "'\\''")}'`;
const execFileAsync = promisify(execFile);
function fixture(t, prefix = "superagent-termux-' ") {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const env = { HOME: path.join(root, 'home'), PREFIX: path.join(root, 'prefix') };
  env.PATH = path.join(env.PREFIX, 'bin');
  fs.mkdirSync(env.PATH, { recursive: true });
  if (process.platform !== 'win32') {
    for (const name of ['sh', 'env']) {
      const executable = termux.findExecutable(name);
      if (executable) fs.symlinkSync(executable, path.join(env.PATH, name));
    }
  }
  return { root, env, output: { write() {} } };
}

test('runit enable/disable persists state, supports SVDIR and a stopped supervisor', async t => {
  const f = fixture(t);
  f.env.SVDIR = path.join(f.root, 'services');
  f.env.LARK_CLI_BIN = '/native/lark-cli';
  const release = path.join(f.env.HOME, '.ov-superagent/current');
  fs.mkdirSync(release, { recursive: true });
  fs.writeFileSync(path.join(release, 'ecosystem.config.cjs'), 'module.exports={apps:[]}');
  const calls = [];
  const options = { env: f.env, homeDir: f.env.HOME, platform: termux,
    output: f.output, execFileImpl: async (...args) => { calls.push(args); } };
  const directory = serviceDirectory(f.env);
  assert.equal(directory, path.join(f.env.SVDIR, 'ov-superagent'));
  assert.equal(await autostartCommand(['enable'], options), 0);
  assert.equal(autostartEnabled(options), true);
  const run = fs.readFileSync(path.join(directory, 'run'), 'utf8');
  assert.match(run, /pm2-runtime/);
  assert.ok(run.includes(quote('LARK_CLI_BIN=/native/lark-cli')));
  assert.doesNotMatch(run, /systemctl|\.vika|VIKA_CONFIG/);
  await autostartCommand(['disable'], options);
  assert.equal(autostartEnabled(options), false);
  assert.deepEqual(calls, []);
  fs.mkdirSync(path.join(directory, 'supervise'));
  fs.writeFileSync(path.join(directory, 'supervise/ok'), '');
  await autostartCommand(['enable'], options);
  assert.equal(autostartEnabled(options), true);
  assert.deepEqual(calls.at(-1).slice(0, 2), ['sv', ['up', directory]]);
  await autostartCommand(['disable'], options);
  assert.equal(autostartEnabled(options), false);
  assert.deepEqual(calls.at(-1).slice(0, 2), ['sv', ['-w', '45', 'down', directory]]);
});

test('runit shares CLI PM2 defaults and captures configuration paths, never API keys', t => {
  const f = fixture(t);
  for (const override of [{}, { PM2_HOME: path.join(f.root, 'custom-pm2') }]) {
    const env = { ...f.env, ...override, PATH: "x'$(touch /should-not-exist)",
      OV_SUPERAGENT_CONFIG: path.join(f.root, "config's.json"),
      OV_SUPERAGENT_SECRETS_FILE: path.join(f.root, 'secrets.env'),
      LD_PRELOAD: "/native/lib/loader's.so", LD_LIBRARY_PATH: '/native/lib',
      TMPDIR: path.join(f.root, 'tmp'), SLACK_CLI_BIN: '/native/slack',
      OPENAI_API_KEY: 'secret-token' };
    const script = runitScript({ homeDir: env.HOME, env });
    assert.ok(script.includes(quote(`PM2_HOME=${pm2Defaults(env).pm2Env.PM2_HOME}`)));
    for (const key of ['PATH', 'TMPDIR', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'SLACK_CLI_BIN',
      'OV_SUPERAGENT_CONFIG', 'OV_SUPERAGENT_SECRETS_FILE']) {
      assert.ok(script.includes(quote(`${key}=${env[key]}`)));
    }
    assert.doesNotMatch(script, /secret-token|OPENAI_API_KEY/);
  }
  const values = allowedPm2Environment({ PREFIX: '/prefix', LD_PRELOAD: '/prefix/lib/termux-exec.so',
    LD_LIBRARY_PATH: '/prefix/lib', TMPDIR: '/prefix/tmp', SECRET: 'x' }, [], { implementation: termux });
  assert.deepEqual(values, { PREFIX: '/prefix', TMPDIR: '/prefix/tmp',
    LD_PRELOAD: '/prefix/lib/termux-exec.so', LD_LIBRARY_PATH: '/prefix/lib' });
});

test('runit rejects relative service, release and PM2 paths before writing a service', async t => {
  const f = fixture(t);
  assert.throws(() => serviceDirectory({}), /absolute PREFIX or SVDIR/);
  assert.throws(() => serviceDirectory({ ...f.env, SVDIR: 'relative' }), /absolute PREFIX or SVDIR/);
  assert.throws(() => runitScript({ homeDir: f.env.HOME, env: { ...f.env, PREFIX: 'relative' } }), /absolute PREFIX/);
  assert.throws(() => runitScript({ homeDir: f.env.HOME, env: { ...f.env, PM2_HOME: 'relative' } }), /PM2_HOME must be absolute/);
  assert.throws(() => runitScript({ homeDir: f.env.HOME,
    env: { ...f.env, OV_SUPERAGENT_UPDATER_RELEASE_LINK: 'relative' } }), /release link must be absolute/);
  await assert.rejects(autostartCommand(['enable'], { env: f.env, homeDir: f.env.HOME,
    platform: termux, output: f.output, execFileImpl: async () => assert.fail('must not invoke sv') }), /ENOENT/);
  assert.equal(fs.existsSync(serviceDirectory(f.env)), false);
});

test('generated service takes over CLI PM2 and stops its real app on SIGTERM', { skip: process.platform === 'win32', timeout: 45_000 }, async t => {
  const f = fixture(t, 'superagent-termux-service-');
  const release = path.join(f.env.HOME, '.ov-superagent/current');
  fs.mkdirSync(path.join(release, 'node_modules'), { recursive: true });
  fs.symlinkSync(path.join(sourceRoot, 'node_modules/pm2'), path.join(release, 'node_modules/pm2'), 'dir');
  const marker = path.join(f.root, 'started.json');
  fs.writeFileSync(path.join(release, 'app.cjs'),
    `require('node:fs').writeFileSync(${JSON.stringify(marker)},JSON.stringify({pid:process.pid,config:process.env.OV_SUPERAGENT_CONFIG}));setInterval(()=>{},1000);`);
  const config = path.join(f.root, 'custom-config.json');
  fs.writeFileSync(config, JSON.stringify({ name: 'termux-fixture' }));
  const ecosystem = path.join(release, 'ecosystem.config.cjs');
  fs.writeFileSync(ecosystem,
    `const {name}=JSON.parse(require('node:fs').readFileSync(process.env.OV_SUPERAGENT_CONFIG));module.exports={apps:[{name,script:__dirname+'/app.cjs',interpreter:${JSON.stringify(process.execPath)}}]};`);
  const env = { ...allowedPm2Environment(process.env, [], { implementation: termux }),
    ...f.env, PATH: process.env.PATH, TMPDIR: os.tmpdir(),
    OV_SUPERAGENT_CONFIG: config, PM2_HOME: path.join(f.root, 'custom-pm2') };
  const pm2 = path.join(release, 'node_modules/pm2/bin/pm2');
  let child;
  let exited;
  try {
    execFileSync(process.execPath, [pm2, 'start', ecosystem], { env, stdio: 'ignore', timeout: 10_000 });
    const pidFile = path.join(env.PM2_HOME, 'pids/termux-fixture-0.pid');
    const oldPid = Number(fs.readFileSync(pidFile, 'utf8').trim());
    const run = path.join(f.root, 'run');
    fs.writeFileSync(run, runitScript({ homeDir: f.env.HOME, env }));
    execFileSync('sh', ['-n', run]);
    child = spawn('sh', [run], { env, stdio: ['ignore', 'pipe', 'pipe'] });
    let log = '';
    child.stdout.on('data', data => { log += data; });
    child.stderr.on('data', data => { log += data; });
    exited = new Promise((resolve, reject) => { child.once('exit', resolve); child.once('error', reject); });
    let started;
    const deadline = Date.now() + 20_000;
    while (true) {
      try { started = JSON.parse(fs.readFileSync(marker, 'utf8')); } catch {}
      if (started?.pid && started.pid !== oldPid) break;
      if (child.exitCode !== null || Date.now() > deadline) throw new Error(`PM2 service did not take over: ${log}`);
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.equal(started.config, config);
    assert.throws(() => process.kill(oldPid, 0), { code: 'ESRCH' });
    process.kill(started.pid, 0);
    child.kill('SIGTERM');
    await exited;
    assert.throws(() => process.kill(started.pid, 0), { code: 'ESRCH' });
  } finally {
    if (child?.exitCode === null) {
      child.kill('SIGTERM');
      await exited;
    }
    execFileSync(process.execPath, [pm2, 'kill'], { env, stdio: 'ignore', timeout: 10_000 });
  }
});

test('native runsv starts, disables and re-enables an isolated real PM2 service', { timeout: 60_000 }, async t => {
  const runsv = termux.findExecutable('runsv');
  const sv = termux.findExecutable('sv');
  const shell = termux.findExecutable('sh');
  if (process.platform === 'win32' || !runsv || !sv || !shell) {
    t.skip('native runsv, sv and a POSIX shell are required');
    return;
  }
  const f = fixture(t, 'ov-runsv-');
  // The fixture supplies native sh/env under its own PREFIX for runsv.
  const release = path.join(f.env.HOME, '.ov-superagent/current');
  fs.mkdirSync(path.join(release, 'node_modules'), { recursive: true });
  fs.symlinkSync(path.join(sourceRoot, 'node_modules/pm2'), path.join(release, 'node_modules/pm2'), 'dir');
  const marker = path.join(f.root, 'started.json');
  fs.writeFileSync(path.join(release, 'app.cjs'),
    `require('node:fs').writeFileSync(${JSON.stringify(marker)},JSON.stringify({pid:process.pid,sentinel:process.env.SENTINEL,nodeOptions:process.env.NODE_OPTIONS,credential:process.env.OPENVIKING_API_KEY,config:process.env.OV_SUPERAGENT_CONFIG}));setInterval(()=>{},1000);`);
  fs.writeFileSync(path.join(release, 'ecosystem.config.cjs'),
    `module.exports={apps:[{name:'runit-fixture',script:__dirname+'/app.cjs',interpreter:${JSON.stringify(process.execPath)}}]};`);
  const env = { ...allowedPm2Environment(process.env, [], { implementation: termux }),
    ...f.env, PATH: process.env.PATH, TMPDIR: os.tmpdir(),
    SVDIR: path.join(f.root, 'services'), PM2_HOME: path.join(f.root, 'pm2'),
    OV_SUPERAGENT_CONFIG: path.join(f.root, "config's.json") };
  const directory = serviceDirectory(env);
  const options = { env, homeDir: env.HOME, platform: termux, output: f.output,
    execFileImpl: (command, args, opts) => {
      assert.equal(command, 'sv');
      assert.equal(args.at(-1), directory, 'only the temporary service may be controlled');
      return execFileAsync(sv, args, { ...opts, timeout: 15_000 });
    } };
  let supervisor;
  let exited;
  let log = '';
  const waitFor = async (check, message) => {
    const deadline = Date.now() + 15_000;
    while (!check()) {
      if (supervisor?.exitCode !== null || Date.now() > deadline) throw new Error(`${message}: ${log}`);
      await new Promise(resolve => setTimeout(resolve, 50));
    }
  };
  const startedPid = () => {
    try { return JSON.parse(fs.readFileSync(marker, 'utf8')).pid; } catch { return null; }
  };
  try {
    assert.equal(await autostartCommand(['enable'], options), 0);
    supervisor = spawn(runsv, [directory], {
      env: { ...env, SENTINEL: 'supervisor-only', NODE_OPTIONS: '--trace-warnings', OPENVIKING_API_KEY: 'supervisor-secret' },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    supervisor.stdout.on('data', data => { log += data; });
    supervisor.stderr.on('data', data => { log += data; });
    exited = new Promise((resolve, reject) => { supervisor.once('exit', resolve); supervisor.once('error', reject); });
    await waitFor(() => fs.existsSync(path.join(directory, 'supervise/ok')) && startedPid(), 'runit did not start PM2');
    const firstPid = startedPid();
    assert.deepEqual(JSON.parse(fs.readFileSync(marker, 'utf8')), { pid: firstPid, config: env.OV_SUPERAGENT_CONFIG });
    process.kill(firstPid, 0);
    assert.equal(autostartEnabled(options), true);
    assert.equal(await autostartCommand(['disable'], options), 0);
    assert.equal(autostartEnabled(options), false);
    assert.ok(fs.existsSync(path.join(directory, 'down')));
    assert.throws(() => process.kill(firstPid, 0), { code: 'ESRCH' });
    fs.rmSync(marker);
    // Disabled state must prevent runsv from respawning the application.
    await new Promise(resolve => setTimeout(resolve, 1200));
    assert.equal(startedPid(), null);
    assert.equal(supervisor.exitCode, null);
    assert.equal(await autostartCommand(['enable'], options), 0);
    await waitFor(() => startedPid() && startedPid() !== firstPid, 'runit did not restart PM2');
    const secondPid = startedPid();
    assert.deepEqual(JSON.parse(fs.readFileSync(marker, 'utf8')), { pid: secondPid, config: env.OV_SUPERAGENT_CONFIG });
    process.kill(secondPid, 0);
    assert.equal(autostartEnabled(options), true);
    env.OV_SUPERAGENT_CONFIG = path.join(f.root, 'updated-config.json');
    assert.equal(await autostartCommand(['enable'], options), 0);
    await waitFor(() => startedPid() && startedPid() !== secondPid, 'runit did not apply the changed config');
    const thirdPid = startedPid();
    assert.deepEqual(JSON.parse(fs.readFileSync(marker, 'utf8')), { pid: thirdPid, config: env.OV_SUPERAGENT_CONFIG });
    assert.throws(() => process.kill(secondPid, 0), { code: 'ESRCH' });
    assert.equal(await autostartCommand(['disable'], options), 0);
    assert.throws(() => process.kill(thirdPid, 0), { code: 'ESRCH' });
  } finally {
    if (supervisor?.exitCode === null) {
      // Shut down this runsv only; never address the host's service inventory.
      await execFileAsync(sv, ['-w', '10', 'down', directory], { env, timeout: 12_000 }).catch(() => {});
      supervisor.kill('SIGTERM');
      await exited;
    }
    execFileSync(process.execPath, [path.join(release, 'node_modules/pm2/bin/pm2'), 'kill'],
      { env, stdio: 'ignore', timeout: 10_000 });
  }
});
