// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and the OpenViking contributors.
// SPDX-License-Identifier: AGPL-3.0
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { installTermux, shellQuote } from '../../../scripts/install-termux.mjs';
import { autostartCommand, autostartEnabled } from '../src/autostart.mjs';
import { pm2Defaults } from '../src/control.mjs';
import { runitScript, serviceDirectory } from '../src/termux-autostart.mjs';
import * as android from '../../daemon/src/platform/android.mjs';
import { allowedPm2Environment } from '../../updater/src/pm2-platform.mjs';

// Only the real shell/symlink/process tests need POSIX, not the policy and lifecycle tests.
const posix = { skip: process.platform === 'win32' };
const sourceRoot = fileURLToPath(new URL('../../../', import.meta.url));
function fixture(t, prefix = "superagent-termux-' ") {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const env = { HOME: path.join(root, 'home'), PREFIX: path.join(root, 'prefix') };
  env.PATH = path.join(env.PREFIX, 'bin');
  fs.mkdirSync(env.PATH, { recursive: true });
  for (const name of ['sh', 'bash', 'npm', 'lark-cli']) fs.writeFileSync(path.join(env.PATH, name), '', { mode: 0o755 });
  const source = path.join(root, 'source');
  fs.mkdirSync(path.join(source, 'packages/cli/bin'), { recursive: true });
  fs.writeFileSync(path.join(source, 'packages/cli/bin/ov-superagent.mjs'), '');
  fs.writeFileSync(path.join(source, 'package.json'), '{}');
  fs.writeFileSync(path.join(source, 'permission.json'), '{"scopes":{}}');
  fs.writeFileSync(path.join(source, '.env'), 'private');
  const calls = [];
  return { root, env, source, calls, platform: 'android',
    run: (...args) => calls.push(args), output: { write() {} } };
}

function noActiveInstall(f) {
  assert.equal(fs.existsSync(path.join(f.env.HOME, '.ov-superagent/current')), false);
  assert.equal(fs.existsSync(path.join(f.env.HOME, '.ov-superagent/bin/ov-superagent')), false);
  assert.deepEqual(fs.readdirSync(path.join(f.env.HOME, '.ov-superagent/releases')), []);
}

test('Termux installs the current CLI and runs its launcher without global Node on PATH', posix, t => {
  const f = fixture(t);
  // Exercise the real source entrypoint, not an empty mock CLI or just sh -n.
  const release = installTermux({ ...f, source: sourceRoot });
  assert.equal(fs.realpathSync(path.join(f.env.HOME, '.ov-superagent/current')), release);
  assert.equal(fs.existsSync(path.join(release, 'node_modules')), false); // npm was stubbed, not copied
  assert.deepEqual(f.calls[1][1], ['ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund']);
  assert.match(f.calls[2][1][0], /check-pi.mjs$/);
  assert.match(f.calls[3][1][0], /check-durable.mjs$/);
  const launcher = path.join(f.env.HOME, '.ov-superagent/bin/ov-superagent');
  const env = { ...process.env, ...f.env };
  const version = JSON.parse(fs.readFileSync(path.join(sourceRoot, 'package.json'))).version;
  const sh = execFileSync('sh', ['-c', 'command -v sh'], { encoding: 'utf8' }).trim();
  assert.equal(execFileSync(sh, [launcher, '--version'], { env, encoding: 'utf8' }).trim(), version);
  execFileSync('sh', ['-n', path.join(release, 'bin/pm2')]);
  assert.throws(() => installTermux(f), /already exists/);
  assert.equal(fs.realpathSync(path.join(f.env.HOME, '.ov-superagent/current')), release);
});

test('failed runtime check leaves no active installation, credentials or staged release', t => {
  const f = fixture(t);
  f.run = (_cmd, args, options) => {
    if (args[0]?.endsWith('check-durable.mjs')) {
      assert.equal(fs.existsSync(path.join(options.cwd, '.env')), false);
      throw new Error('broken durable');
    }
  };
  assert.throws(() => installTermux(f), /broken durable/);
  noActiveInstall(f);
});

test('missing CLI entrypoint is refused before activation and cleans the staged release', t => {
  const f = fixture(t);
  fs.rmSync(path.join(f.source, 'packages/cli/bin/ov-superagent.mjs'));
  assert.throws(() => installTermux(f), { code: 'ENOENT' });
  noActiveInstall(f);
});

test('activation refuses a raced launcher without removing it or leaving a release link', posix, t => {
  const f = fixture(t);
  const launcher = path.join(f.env.HOME, '.ov-superagent/bin/ov-superagent');
  f.run = (_cmd, args) => {
    if (args[0]?.endsWith('check-durable.mjs')) {
      fs.mkdirSync(path.dirname(launcher), { recursive: true });
      fs.writeFileSync(launcher, 'other installation');
    }
  };
  assert.throws(() => installTermux(f), { code: 'EEXIST' });
  assert.equal(fs.readFileSync(launcher, 'utf8'), 'other installation');
  assert.equal(fs.existsSync(path.join(f.env.HOME, '.ov-superagent/current')), false);
  assert.deepEqual(fs.readdirSync(path.join(f.env.HOME, '.ov-superagent/releases')), []);
});

test('non-Android install is refused before invoking dependencies', t => {
  const f = fixture(t);
  assert.throws(() => installTermux({ ...f, platform: 'linux' }), /native Termux/);
  assert.deepEqual(f.calls, []);
});

test('runit enable/disable persists state, supports SVDIR and a stopped supervisor', async t => {
  const f = fixture(t);
  f.env.SVDIR = path.join(f.root, 'services');
  const release = path.join(f.env.HOME, '.ov-superagent/current');
  fs.mkdirSync(release, { recursive: true });
  fs.writeFileSync(path.join(release, 'termux-install.json'), JSON.stringify({ node: process.execPath, larkCli: '/native/lark-cli' }));
  const calls = [];
  const options = { env: f.env, homeDir: f.env.HOME, platform: android,
    output: f.output, execFileImpl: async (...args) => { calls.push(args); } };
  const directory = serviceDirectory(f.env);
  assert.equal(directory, path.join(f.env.SVDIR, 'ov-superagent'));
  assert.equal(await autostartCommand(['enable'], options), 0);
  assert.equal(autostartEnabled(options), true);
  const run = fs.readFileSync(path.join(directory, 'run'), 'utf8');
  assert.match(run, /pm2-runtime/);
  assert.ok(run.includes("export LARK_CLI_BIN='/native/lark-cli'"));
  assert.doesNotMatch(run, /systemctl|\.vika|VIKA_CONFIG/);
  await autostartCommand(['disable'], options);
  assert.equal(autostartEnabled(options), false);
  assert.deepEqual(calls, []); // no sv required when termux-services is stopped
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
      OV_SUPERAGENT_SECRETS_FILE: path.join(f.root, 'secrets.env'), OPENAI_API_KEY: 'secret-token' };
    const script = runitScript({ homeDir: env.HOME, env });
    assert.ok(script.includes(`export PM2_HOME=${shellQuote(pm2Defaults(env).pm2Env.PM2_HOME)}\n`));
    for (const key of ['PATH', 'OV_SUPERAGENT_CONFIG', 'OV_SUPERAGENT_SECRETS_FILE']) {
      assert.ok(script.includes(`export ${key}=${shellQuote(env[key])}\n`));
    }
    assert.doesNotMatch(script, /secret-token|OPENAI_API_KEY/);
  }
  const values = allowedPm2Environment({ PREFIX: '/prefix', LD_PRELOAD: '/prefix/lib/termux-exec.so',
    LD_LIBRARY_PATH: '/prefix/lib', TMPDIR: '/prefix/tmp', SECRET: 'x' }, [], { implementation: android });
  assert.deepEqual(values, { PREFIX: '/prefix', TMPDIR: '/prefix/tmp',
    LD_PRELOAD: '/prefix/lib/termux-exec.so', LD_LIBRARY_PATH: '/prefix/lib' });
});

test('generated service takes over CLI PM2 and stops its real app on SIGTERM', { ...posix, timeout: 45_000 }, async t => {
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
  const env = { ...f.env, PATH: process.env.PATH, TMPDIR: os.tmpdir(),
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
