// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

test('manifest and release helpers use the same explicit HOME override', () => {
  const home = path.join(os.tmpdir(), 'vika-custom-home');
  const profile = path.join(os.tmpdir(), 'vika-other-profile');
  const manifest = new URL('../src/manifest.mjs', import.meta.url).href;
  const targets = new URL('../../updater/src/targets.mjs', import.meta.url).href;
  const script = `
    import { vikaHome, manifestPath } from ${JSON.stringify(manifest)};
    import { defaultReleaseLink, defaultSocketPath } from ${JSON.stringify(targets)};
    console.log(JSON.stringify([vikaHome(), manifestPath(), defaultReleaseLink(), defaultSocketPath()]));
  `;
  const env = { ...process.env, HOME: home, USERPROFILE: profile };
  delete env.VIKA_CONFIG;
  const actual = JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e', script], { env, encoding: 'utf8' }));
  assert.deepEqual(actual, [path.join(home, '.vika'), path.join(home, '.vika', 'config.json'),
    path.join(home, '.vika', 'current'), path.join(home, '.vika', 'updater.sock')]);
});
