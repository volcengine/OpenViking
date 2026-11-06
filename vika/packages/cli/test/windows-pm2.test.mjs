import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { promisify } from 'node:util';
import { execFile } from '../../daemon/src/platform.mjs';
import { pm2Defaults } from '../src/control.mjs';
import { cleanPm2ControlEnv, startApp, pm2Processes, verifyPm2App } from '../../updater/src/pm2.mjs';
const { pipePaths } = createRequire(import.meta.url)('../../../scripts/pm2-windows.cjs');

test('PM2 Windows pipes are scoped to home and control environment preserves OS essentials', () => {
  assert.deepEqual(pipePaths('C:\\Users\\Alice\\.vika\\pm2'), pipePaths('c:\\users\\alice\\.vika\\pm2'));
  assert.notEqual(pipePaths('C:\\first').DAEMON_RPC_PORT, pipePaths('C:\\second').DAEMON_RPC_PORT);
  assert.notEqual(pipePaths('C:\\first').DAEMON_RPC_PORT, '\\\\.\\pipe\\rpc.sock');
  const env = cleanPm2ControlEnv({ SystemRoot: 'C:\\Windows', USERPROFILE: 'C:\\Users\\Alice', APPDATA: 'roaming', TEMP: 'temp', ComSpec: 'cmd.exe', UNRELATED_SECRET: 'excluded' });
  assert.equal(env.SYSTEMROOT, 'C:\\Windows');
  assert.equal(env.COMSPEC, 'cmd.exe');
  assert.equal(env.APPDATA, 'roaming');
  assert.equal(env.TEMP, 'temp');
  assert.equal(env.UNRELATED_SECRET, undefined);
});

test('Windows PM2 starts, verifies and gracefully stops only an isolated fixture', { skip: process.platform !== 'win32', timeout: 90000 }, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), '维京 PM2 fixture '));
  const release = path.join(root, 'release');
  const pm2Home = path.join(root, 'pm2');
  fs.mkdirSync(release);
  const readyFile = path.join(root, 'ready.json');
  const stoppedFile = path.join(root, 'stopped');
  const script = path.join(release, 'fixture.cjs');
  fs.writeFileSync(script, `const fs = require('node:fs'); const http = require('node:http');
const server = http.createServer((req,res) => res.end('ready'));
server.listen(0, '127.0.0.1', () => { fs.writeFileSync(${JSON.stringify(readyFile)}, JSON.stringify({port:server.address().port})); process.send('ready'); });
process.on('message', message => { if(message === 'shutdown') { fs.writeFileSync(${JSON.stringify(stoppedFile)}, 'graceful'); server.close(() => { if(process.connected) process.disconnect(); }); } });`);
  const configPath = path.join(release, 'ecosystem.config.cjs');
  const app = `vika-windows-test-${path.basename(root).slice(-6)}`;
  fs.writeFileSync(configPath, `module.exports = ${JSON.stringify({ apps: [{ name: app, script, interpreter: process.execPath, cwd: release, exec_mode: 'fork', instances: 1, wait_ready: true, autorestart: false, shutdown_with_message: true, kill_timeout: 5000 }] })};`);
  const context = { ...pm2Defaults({ ...process.env, PM2_HOME: pm2Home }), execFileImpl: promisify(execFile) };
  const run = args => context.execFileImpl(context.pm2Bin, args, { env: context.pm2Env, timeout: 20000 });
  let daemonPid;
  try {
    assert.deepEqual(await pm2Processes(context), []);
    daemonPid = Number(fs.readFileSync(path.join(pm2Home, 'pm2.pid'), 'utf8').trim());
    assert.ok(Number.isSafeInteger(daemonPid) && daemonPid > 0);
    await startApp({ app, readyUrl: 'http://127.0.0.1/ready' }, {
      ...context, release, configPath,
      fetchImpl: () => fetch(`http://127.0.0.1:${JSON.parse(fs.readFileSync(readyFile, 'utf8')).port}/ready`),
      readyOptions: { attempts: 20, intervalMs: 100 },
    });
    assert.equal((await pm2Processes(context)).find(info => info.name === app)?.pm2_env.status, 'online');
    await run(['stop', app]);
    assert.equal(fs.readFileSync(stoppedFile, 'utf8'), 'graceful');
    assert.equal((await pm2Processes(context)).find(info => info.name === app)?.pm2_env.status, 'stopped');
  } finally {
    // Exact fixture name and fresh per-test PM2 home; never issue `kill`/`all`.
    if (!daemonPid) {
      try { daemonPid = Number(fs.readFileSync(path.join(pm2Home, 'pm2.pid'), 'utf8').trim()); } catch {}
    }
    if (Number.isSafeInteger(daemonPid) && daemonPid > 0) await run(['delete', app]).catch(() => {});
    if (Number.isSafeInteger(daemonPid) && daemonPid > 0) { try { process.kill(daemonPid); } catch {} }
    await new Promise(resolve => setTimeout(resolve, 300));
    fs.rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});

test('release verification canonicalizes a junction and still refuses external scripts', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-release-alias-'));
  try {
    const release = path.join(root, 'release');
    const alias = path.join(root, 'current');
    const script = path.join(release, 'main.cjs');
    const outside = path.join(root, 'outside.cjs');
    fs.mkdirSync(release);
    fs.writeFileSync(script, '');
    fs.writeFileSync(outside, '');
    fs.symlinkSync(release, alias, process.platform === 'win32' ? 'junction' : 'dir');
    const verify = pm_exec_path => verifyPm2App({
      app: 'fixture', release: alias, pm2Bin: 'unused',
      execFileImpl: async () => ({ stdout: JSON.stringify([{ name: 'fixture', pm2_env: { status: 'online', pm_exec_path } }]) }),
    });
    assert.equal((await verify(script)).name, 'fixture');
    await assert.rejects(verify(outside), /outside the current release/);
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
