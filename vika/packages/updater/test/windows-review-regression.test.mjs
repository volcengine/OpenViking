// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { PLATFORMS } from '../src/artifacts.mjs';

const ecosystem = fileURLToPath(new URL('../../../ecosystem.config.cjs', import.meta.url));
test('Windows source support does not add a release platform', () => {
  assert.deepEqual(Object.keys(PLATFORMS).sort(), ['darwin-arm64', 'darwin-x64', 'linux-arm64', 'linux-x64']);
});

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
