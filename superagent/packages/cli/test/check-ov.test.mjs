// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { checkInstallationOV } from '../src/check-ov.mjs';

test('installation requires OV before the first setup', async (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-ov-install-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const env = { HOME: home };
  await assert.rejects(checkInstallationOV({ env, detect: async () => ({ usable: false, reason: 'offline' }) }), /OpenViking/);
  await checkInstallationOV({ env, detect: async () => ({ usable: true }) });
});

test('installation checks each existing instance with its pinned OV config', async (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-ov-install-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  const manifest = path.join(home, 'config.json');
  fs.writeFileSync(manifest, JSON.stringify({ version: 1, env: {}, defaultInstance: 'a', agents: ['a', 'b'].map((id, i) => ({
    id, displayName: id, channel: 'weixin', connectors: ['weixin'], model: 'pi', port: 3738 + i,
    dataDir: path.join(home, id, 'data'), workspace: path.join(home, id, 'ws'), ovConfig: path.join(home, id, 'ov.json'),
  })) }));
  const checked = [];
  await assert.rejects(checkInstallationOV({ env: { HOME: home, VIKA_CONFIG: manifest }, detect: async ({ configPath }) => {
    checked.push(configPath);
    return { usable: checked.length === 1, reason: 'offline' };
  } }), /b.*offline/);
  assert.deepEqual(checked, ['a', 'b'].map((id) => path.join(home, id, 'ov.json')));
});
