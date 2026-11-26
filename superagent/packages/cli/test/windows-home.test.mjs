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
    import { defaultReleaseLink, defaultUpdaterEndpoint } from ${JSON.stringify(targets)};
    console.log(JSON.stringify([superagentHome(), manifestPath(), defaultReleaseLink(), defaultUpdaterEndpoint()]));
  `;
  const env = { ...process.env, HOME: home, USERPROFILE: profile };
  delete env.OV_SUPERAGENT_CONFIG;
  const actual = JSON.parse(execFileSync(process.execPath, ['--input-type=module', '-e', script], { env, encoding: 'utf8' }));
  const [dir, config, releaseLink, updaterEndpoint] = actual;
  assert.deepEqual([dir, config, releaseLink], [path.join(home, '.ov-superagent'),
    path.join(home, '.ov-superagent', 'config.json'), path.join(home, '.ov-superagent', 'current')]);
  // Windows has no socket files: the updater endpoint is a stable named pipe.
  if (process.platform === 'win32') assert.match(updaterEndpoint, /^\\\\\.\\pipe\\superagent-updater-[0-9a-f]{24}$/);
  else assert.equal(updaterEndpoint, path.join(home, '.ov-superagent', 'updater.sock'));
});
