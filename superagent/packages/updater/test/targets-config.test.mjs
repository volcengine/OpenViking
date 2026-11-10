// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';
import { fileURLToPath } from 'node:url';

const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-targets-'));
const manifestPath = path.join(directory, 'config.json');
fs.writeFileSync(manifestPath, `${JSON.stringify({
  version: 1,
  defaultInstance: 'beta',
  env: {},
  agents: ['alpha', 'beta', 'gamma'].map((id, index) => ({
    id,
    displayName: id.toUpperCase(),
    profile: `cli_${id}`,
    port: 3738 + index,
    dataDir: `/srv/vika/data/${id}`,
    workspace: `/srv/vika/workspaces/${id}`,
    ovConfig: `/srv/vika/ov/${id}.json`,
    model: 'gpt-5.6-sol',
  })),
}, null, 2)}\n`);
delete process.env.VIKA_UPDATER_TARGETS_JSON;
process.env.VIKA_CONFIG = manifestPath;

const {
  assertUpgradeTarget,
  loadTargets,
  shouldUpgradeSharedWeb,
  upgradeTargetNames,
} = await import('../src/targets.mjs');
after(() => fs.rmSync(directory, { recursive: true, force: true }));

test('updater derives every allowlisted target from the deployment manifest', () => {
  assert.deepEqual(upgradeTargetNames(), ['alpha', 'beta', 'gamma', 'all']);
  assert.deepEqual(loadTargets().beta, {
    app: 'vika-beta',
    config: 'ecosystem.config.cjs',
    readyUrl: 'http://127.0.0.1:3739/ready',
    upgradeSharedWeb: false,
  });
  assert.equal(assertUpgradeTarget('gamma'), 'gamma');
  assert.equal(shouldUpgradeSharedWeb('alpha'), false);
  assert.equal(shouldUpgradeSharedWeb('all'), true);
  assert.throws(() => assertUpgradeTarget('not-configured'), /alpha, beta, gamma, all/);
});

test('an instance added to the manifest is a target without restarting the updater', () => {
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  manifest.agents.push({ ...manifest.agents[0], id: 'delta', profile: 'cli_delta', port: 3750,
    dataDir: '/srv/vika/data/delta', workspace: '/srv/vika/workspaces/delta', ovConfig: '/srv/vika/ov/delta.json' });
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
  assert.equal(assertUpgradeTarget('delta'), 'delta');
  assert.equal(loadTargets().delta.app, 'vika-delta');
});
