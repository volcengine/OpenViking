// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import test from 'node:test';
import { writeManifest } from '../src/deployment-manifest.mjs';
import { runtimeManifestSelector, updateRuntimePreference } from '../src/runtime-preference.mjs';

const require = createRequire(import.meta.url);
const { applyDeploymentAgentEnv, loadDeploymentManifest } = require('../../../deployment-config.cjs');

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-runtime-preference-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const manifestPath = path.join(directory, 'config.json');
  const agent = (id, port) => ({
    id,
    displayName: id,
    channel: 'lark',
    connectors: ['lark'],
    profile: `profile-${id}`,
    port,
    dataDir: path.join(directory, id, 'data'),
    workspace: path.join(directory, id, 'workspace'),
    ovConfig: path.join(directory, id, 'ov.json'),
    model: 'codex:',
  });
  const first = agent('first', 3738);
  const second = agent('second', 3739);
  const manifest = { version: 1, defaultInstance: 'first', env: {}, agents: [first, second] };
  writeManifest(manifestPath, manifest);
  const config = {
    manifestPath,
    instanceId: second.id,
    dataDir: second.dataDir,
    initialModel: second.model,
  };
  return {
    config,
    manifest,
    manifestPath,
    read: () => JSON.parse(fs.readFileSync(manifestPath, 'utf8')),
  };
}

test('uses the same stable runtime selectors as setup', () => {
  assert.equal(runtimeManifestSelector('codex'), 'codex:');
  assert.equal(runtimeManifestSelector('claude'), 'claude:');
  assert.equal(runtimeManifestSelector('pi'), 'pi');
  assert.equal(runtimeManifestSelector('traex'), 'traex');
  assert.equal(runtimeManifestSelector('agy'), 'agy');
  assert.equal(runtimeManifestSelector('volc'), 'ark');
  assert.throws(() => runtimeManifestSelector('missing'), /Unknown runtime/);
});

test('a live runtime switch updates only its instance and survives config reload', (t) => {
  const f = fixture(t);
  const target = updateRuntimePreference(f.config, 'pi');
  assert.equal(target, 'pi');
  assert.equal(f.config.initialModel, 'pi');
  assert.deepEqual(f.read(), {
    ...f.manifest,
    agents: [f.manifest.agents[0], { ...f.manifest.agents[1], model: 'pi' }],
  });
  const env = {};
  applyDeploymentAgentEnv(env, loadDeploymentManifest({ VIKA_CONFIG: f.manifestPath }), 'second');
  assert.equal(env.VIKA_MODEL, 'pi');
  assert.equal(fs.statSync(f.manifestPath).mode & 0o777, 0o600);
});

test('a repeated runtime selection does not rewrite the manifest', (t) => {
  const f = fixture(t);
  updateRuntimePreference(f.config, 'pi');
  const before = fs.statSync(f.manifestPath);
  const backup = fs.readFileSync(`${f.manifestPath}.bak`, 'utf8');
  updateRuntimePreference(f.config, 'pi');
  assert.equal(fs.statSync(f.manifestPath).ino, before.ino);
  assert.equal(fs.readFileSync(`${f.manifestPath}.bak`, 'utf8'), backup);
});

test('a repeated runtime selection preserves an explicit configured model', (t) => {
  const f = fixture(t);
  const changed = f.read();
  changed.agents[1].model = 'codex:gpt-5.6-sol';
  writeManifest(f.manifestPath, changed);
  f.config.initialModel = 'codex:gpt-5.6-sol';
  const before = fs.statSync(f.manifestPath);
  assert.equal(updateRuntimePreference(f.config, 'codex'), 'codex:gpt-5.6-sol');
  assert.equal(f.config.initialModel, 'codex:gpt-5.6-sol');
  assert.equal(fs.statSync(f.manifestPath).ino, before.ino);
  assert.deepEqual(f.read(), changed);
});

test('runtime persistence preserves unrelated edits and rejects a stale instance', (t) => {
  const f = fixture(t);
  const changed = f.read();
  changed.agents[0].displayName = 'Changed elsewhere';
  changed.env.VIKA_LOG_LEVEL = 'warn';
  writeManifest(f.manifestPath, changed);
  updateRuntimePreference(f.config, 'claude');
  changed.agents[1].model = 'claude:';
  assert.deepEqual(f.read(), changed);

  const stale = f.read();
  stale.agents[1].model = 'pi';
  writeManifest(f.manifestPath, stale);
  assert.throws(() => updateRuntimePreference(f.config, 'traex'), /changed elsewhere/);
  assert.deepEqual(f.read(), stale);
  assert.equal(f.config.initialModel, 'claude:');
});
