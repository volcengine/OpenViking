// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

test('manifest and release helpers use the same explicit HOME override', () => {
  const home = path.join(os.tmpdir(), 'superagent-custom-home');
  const profile = path.join(os.tmpdir(), 'superagent-other-profile');
  const manifest = new URL('../src/manifest.mjs', import.meta.url).href;
  const targets = new URL('../../updater/src/targets.mjs', import.meta.url).href;
  const script = `
    import { superagentHome, manifestPath } from ${JSON.stringify(manifest)};
    import { defaultReleaseLink, defaultSocketPath } from ${JSON.stringify(targets)};
    console.log(JSON.stringify([superagentHome(), manifestPath(), defaultReleaseLink(), defaultSocketPath()]));
  `;
  const env = { ...process.env, HOME: home, USERPROFILE: profile };
  delete env.OV_SUPERAGENT_CONFIG;
  const actual = JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e', script], { env, encoding: 'utf8' }));
  assert.deepEqual(actual, [path.join(home, '.ov-superagent'), path.join(home, '.ov-superagent', 'config.json'),
    path.join(home, '.ov-superagent', 'current'), path.join(home, '.ov-superagent', 'updater.sock')]);
});
