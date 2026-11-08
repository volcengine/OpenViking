// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ecosystem = fileURLToPath(new URL('../../../ecosystem.config.cjs', import.meta.url));

test('ecosystem excludes updater only on Windows and preserves mixed-case Path', (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-ecosystem-review-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const config = path.join(home, 'config.json');
  fs.writeFileSync(config, JSON.stringify({ version: 1, env: {}, defaultInstance: 'primary', agents: [{
    id: 'primary', displayName: 'Primary', profile: 'test', port: 3738, dataDir: path.join(home, 'data'),
    workspace: path.join(home, 'work'), ovConfig: path.join(home, 'ov.json'), model: 'durable',
  }] }));
  for (const platform of ['linux', 'darwin', 'win32']) {
    const output = execFileSync(process.execPath, ['-e', `
      Object.defineProperty(process, 'platform', {value: ${JSON.stringify(platform)}});
      const {buildEcosystem} = require(${JSON.stringify(ecosystem)});
      console.log(JSON.stringify(buildEcosystem({HOME: process.env.HOME, VIKA_CONFIG: process.env.VIKA_CONFIG, Path: 'inherited-path'})));
    `], { env: { ...process.env, HOME: home, VIKA_CONFIG: config }, encoding: 'utf8' });
    const apps = JSON.parse(output).apps;
    assert.equal(apps.some(app => app.name === 'vika-updater'), platform !== 'win32');
    const daemon = apps.find(app => app.name === 'vika-primary');
    if (platform === 'win32') assert.equal(daemon.env.PATH, 'inherited-path');
    else assert.equal(daemon.env.PATH, ':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin');
  }
});

test('Linux and macOS config selection match the full upstream POSIX snapshot (host OS is not simulated execution)', { skip: process.platform === 'win32' }, t => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-posix-snapshot-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const config = path.join(home, 'config.json');
  fs.writeFileSync(config, JSON.stringify({ version: 1, env: {}, defaultInstance: 'primary', agents: [{
    id: 'primary', displayName: 'Primary', profile: 'test', port: 3738, dataDir: path.join(home, 'data'),
    workspace: path.join(home, 'work'), ovConfig: path.join(home, 'ov.json'), model: 'durable',
  }] }));
  const source = { HOME: home, VIKA_CONFIG: config, PATH: '/fixture/bin', USERPROFILE: '/ignored', Path: '/ignored',
    TEMP: '/ignored', TMP: '/ignored', SYSTEMROOT: '/ignored', APPDATA: '/ignored', LOCALAPPDATA: '/ignored',
    COMSPEC: '/ignored', PATHEXT: '/ignored', HOMEDRIVE: '/ignored', HOMEPATH: '/ignored', USERNAME: '/ignored',
    UNRELATED_SECRET: 'must-not-propagate' };
  // Captured from origin/feat/vika a6c43c131047, not from the adapter under test.
  const expected = JSON.parse(fs.readFileSync(new URL('./fixtures/ecosystem-posix.json', import.meta.url), 'utf8'));
  const pm2 = fileURLToPath(new URL('../src/pm2.mjs', import.meta.url));
  for (const platform of ['linux', 'darwin']) {
    const output = execFileSync(process.execPath, ['-e', `
      Object.defineProperty(process, 'platform', {value: ${JSON.stringify(platform)}});
      const {buildEcosystem} = require(${JSON.stringify(ecosystem)});
      const {cleanPm2ControlEnv} = require(${JSON.stringify(pm2)});
      const source = ${JSON.stringify(source)};
      console.log(JSON.stringify({ecosystem:buildEcosystem(source),control:cleanPm2ControlEnv(source)}));
    `], { env: { ...process.env, HOME: home, VIKA_CONFIG: config }, encoding: 'utf8' });
    const result = JSON.parse(output);
    const normalized = JSON.parse(JSON.stringify(result.ecosystem).replaceAll(home, '<HOME>')
      .replaceAll(path.dirname(ecosystem), '<RELEASE>').replaceAll(process.execPath, '<NODE>'));
    assert.deepEqual(normalized, expected, platform);
    assert.deepEqual(result.control, { HOME: home, PATH: '/fixture/bin', VIKA_CONFIG: config }, platform);
  }
});

test('PM2 helpers use injected OS primitives while preserving environment whitelist behavior', async () => {
  const posix = await import('../../daemon/src/platform/posix.mjs');
  const windows = await import('../../daemon/src/platform/win32.mjs');
  const { allowedPm2Environment, ecosystemSearchPath, pm2Bin, pm2Options } = await import('../src/pm2-platform.mjs');
  const env = { HOME: '/home/test', Path: 'inherited', PATH: 'explicit', Temp: 'scratch', HomeDrive: 'C:', vika_log_level: 'debug', UNRELATED_SECRET: 'hidden' };
  const keys = ['HOME', 'PATH', 'VIKA_LOG_LEVEL'];
  assert.deepEqual(allowedPm2Environment(env, keys, { implementation: posix }), { HOME: '/home/test', PATH: 'explicit' });
  assert.deepEqual(allowedPm2Environment(env, keys, { implementation: windows }), {
    HOME: '/home/test', PATH: 'explicit', VIKA_LOG_LEVEL: 'debug', TEMP: 'scratch', HOMEDRIVE: 'C:',
  });
  assert.equal(ecosystemSearchPath({ Path: 'inherited' }, '/fallback', windows), 'inherited');
  assert.equal(ecosystemSearchPath({ Path: 'inherited' }, '/fallback', posix), ':/fallback');
  assert.deepEqual(pm2Options(posix), {});
  assert.deepEqual(pm2Options(windows), { shutdown_with_message: true });
  assert.equal(pm2Bin('/release', posix), path.join('/release', 'bin', 'pm2'));
  assert.equal(pm2Bin('/release', windows), 'pm2');
});
