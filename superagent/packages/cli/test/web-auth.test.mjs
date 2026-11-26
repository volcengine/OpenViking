// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import { supportsFileModes } from '../../daemon/src/platform/index.mjs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { readManifest, writeManifest } from '../src/manifest.mjs';
import { webAuthCommand } from '../src/web-auth.mjs';

test('web auth saves the key and host, keeps other secrets, restarts the Web, and shows them', async (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-web-auth-'));
  t.after(() => fs.rmSync(home, { force: true, recursive: true }));
  const env = { HOME: home, PATH: '' };
  writeManifest(path.join(home, '.ov-superagent', 'config.json'), {
    version: 1, defaultInstance: 'alpha', env: {},
    agents: [{
      id: 'alpha', displayName: 'Alpha', channel: 'weixin', connectors: ['weixin'], model: 'codex:', port: 3738,
      dataDir: path.join(home, '.ov-superagent', 'data', 'alpha'), workspace: path.join(home, 'workspace', 'alpha'),
      ovConfig: path.join(home, '.ov-superagent', 'ov', 'alpha.json'),
    }],
  });
  const secrets = path.join(home, '.ov-superagent', 'secrets.env');
  fs.writeFileSync(secrets, 'ARK_API_KEY=keep-me\nOV_SUPERAGENT_WEB_API_KEY=old-key-0123456789\n');
  const out = [];
  let restarts = 0;
  const options = { env, stdout: { write: (text) => out.push(text) }, restart: async () => { restarts += 1; return 0; } };

  assert.equal(await webAuthCommand(['--host', '0.0.0.0'], options), 0);
  const key = fs.readFileSync(secrets, 'utf8').match(/^ARK_API_KEY=keep-me\nOV_SUPERAGENT_WEB_API_KEY=([\w-]{43})\n$/)[1];
  if (supportsFileModes) assert.equal(fs.statSync(secrets).mode & 0o777, 0o600);
  assert.deepEqual(readManifest(env).value.web, { host: '0.0.0.0' });
  assert.equal(restarts, 1);

  await assert.rejects(webAuthCommand(['--key', 'short'], options), /16/);
  out.length = 0;
  assert.equal(await webAuthCommand(['--show'], options), 0);
  assert.equal(out.join(''), `host: 0.0.0.0\nOV_SUPERAGENT_WEB_API_KEY: ${key}\n`);
});
