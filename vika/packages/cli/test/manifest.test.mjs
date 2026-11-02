// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { updateManifest } from '../../daemon/src/deployment-manifest.mjs';
import { InstanceLock } from '../../daemon/src/lock.mjs';
import {
  allocatePort, instanceIdProblem, readManifest, suggestInstanceId, validateManifest, withDefaultTempDir, withInstance, writeManifest,
} from '../src/manifest.mjs';

function agent(id, overrides = {}) {
  return {
    id,
    displayName: 'Vika',
    channel: 'lark',
    connectors: ['lark'],
    profile: `vika-${id}`,
    port: 3738,
    dataDir: `/srv/vika/data/${id}`,
    workspace: `/srv/vika/workspace/${id}`,
    ovConfig: `/srv/vika/ov/${id}.json`,
    model: 'codex:',
    ...overrides,
  };
}

function tempHome(t) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-manifest-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  return home;
}

test('setup defaults only /root data paths to a per-instance /tmp directory and preserves explicit choices', () => {
  for (const dataDir of ['/root', '/root/.vika/data/primary', '/root/.local/share/vika-primary', '/srv/../root/data']) {
    const original = agent('primary', { dataDir });
    const configured = withDefaultTempDir(original, { TMPDIR: '/root/tmp' });
    assert.deepEqual(configured, { ...original, tempDir: '/tmp/vika-primary' });
    assert.equal(original.tempDir, undefined);
    assert.equal(withDefaultTempDir({ ...original, id: 'secondary' }).tempDir, '/tmp/vika-secondary');
    assert.equal(withDefaultTempDir(original, { VIKA_TEMP_DIR: '/tmp/custom' }), original);
    const custom = { ...original, tempDir: '/tmp/my-agent' };
    assert.equal(withDefaultTempDir(custom, { VIKA_TEMP_DIR: '/tmp/global' }), custom);
  }
  for (const dataDir of ['/root-other/data', '/srv/root/data', '/home/alice/.vika/data/primary']) {
    const original = agent('primary', { dataDir });
    assert.equal(withDefaultTempDir(original), original);
  }
});

test('instance ids follow the manifest rules and stay unique', () => {
  const manifest = { agents: [agent('primary')] };
  assert.equal(instanceIdProblem('agent2', manifest), null);
  assert.match(instanceIdProblem('primary', manifest), /已经有/);
  assert.match(instanceIdProblem('web', manifest), /保留/);
  assert.match(instanceIdProblem('Bad_Id', manifest), /小写字母/);
  assert.equal(suggestInstanceId(null), 'primary');
  assert.equal(suggestInstanceId({ agents: [agent('primary'), agent('agent2')] }), 'agent3');
});

test('a new instance gets the lowest port that no instance, the Web, or another process holds', async () => {
  const manifest = { agents: [agent('primary', { port: 3738 })] };
  assert.equal(await allocatePort(manifest, async (port) => port !== 3739), 3740);
  assert.equal(await allocatePort(null, async () => true), 3738);
});

test('shared env only gains keys it does not have yet', () => {
  const first = withInstance(null, agent('primary'), { LARK_CLI_BIN: '/usr/bin/lark-cli' });
  assert.equal(first.defaultInstance, 'primary');
  assert.equal(first.version, 1);
  const second = withInstance(first, agent('agent2', { port: 3739 }), { LARK_CLI_BIN: '/other/lark-cli', CODEX_BIN: '/bin/codex' });
  assert.deepEqual(second.env, { LARK_CLI_BIN: '/usr/bin/lark-cli', CODEX_BIN: '/bin/codex' });
  assert.equal(second.defaultInstance, 'primary');
  assert.equal(first.agents.length, 1, 'the original manifest is not mutated');
});

test('only a manifest the daemon accepts is saved; the previous one is kept as .bak', (t) => {
  const home = tempHome(t);
  const filePath = path.join(home, '.vika', 'config.json');
  const first = withInstance(null, agent('primary'));
  writeManifest(filePath, first);
  assert.equal(fs.statSync(filePath).mode & 0o777, 0o600);
  assert.deepEqual(readManifest({ HOME: home }).value, first);

  const overlapping = withInstance(first, agent('agent2', { port: 3739, workspace: '/srv/vika/data/primary/inner' }));
  assert.throws(() => validateManifest(filePath, overlapping), /overlap|nest|inside/i);
  assert.throws(() => writeManifest(filePath, overlapping), /overlap|nest|inside/i);
  assert.deepEqual(JSON.parse(fs.readFileSync(filePath, 'utf8')), first);
  assert.deepEqual(fs.readdirSync(path.dirname(filePath)).sort(), ['config.json']);

  const second = withInstance(first, agent('agent2', { port: 3739 }));
  writeManifest(filePath, second);
  assert.deepEqual(JSON.parse(fs.readFileSync(`${filePath}.bak`, 'utf8')), first);
});

test('setup refuses to build on a manifest that does not load', (t) => {
  const home = tempHome(t);
  fs.mkdirSync(path.join(home, '.vika'));
  fs.writeFileSync(path.join(home, '.vika', 'config.json'), '{"version": 2}');
  assert.throws(() => readManifest({ HOME: home }), /version must be 1/);
  assert.deepEqual(readManifest({ HOME: path.join(home, 'nobody') }).value, null);
});

test('setup cannot overwrite a live primary change from a stale snapshot, and writers share a lock', (t) => {
  const filePath = path.join(tempHome(t), 'config.json');
  const first = withInstance(null, agent('primary', { connectors: ['lark', 'telegram'] }));
  writeManifest(filePath, first, { expected: null });
  updateManifest(filePath, (value) => { value.agents[0].channel = 'telegram'; });
  const current = JSON.parse(fs.readFileSync(filePath, 'utf8'));
  assert.throws(() => writeManifest(filePath, first, { expected: first }), /配置已被其他操作修改/);
  assert.deepEqual(JSON.parse(fs.readFileSync(filePath, 'utf8')), current);
  const lock = new InstanceLock(`${filePath}.lock`);
  lock.acquire();
  try {
    assert.throws(() => writeManifest(filePath, first), /already|another/);
    assert.throws(() => updateManifest(filePath, () => assert.fail('must not edit')), /already|another/);
    assert.deepEqual(JSON.parse(fs.readFileSync(filePath, 'utf8')), current);
  } finally { lock.release(); }
  writeManifest(filePath, first, { expected: current });
  assert.deepEqual(JSON.parse(fs.readFileSync(filePath, 'utf8')), first);
});

test('Slack can be primary or secondary; Ark is rejected', (t) => {
  const home = tempHome(t);
  const filename = path.join(home, '.vika', 'config.json');
  const sl = agent('sl', { channel: 'slack', connectors: ['slack'], profile: undefined, model: 'pi' });
  assert.doesNotThrow(() => validateManifest(filename, withInstance(null, sl)));
  assert.doesNotThrow(() => validateManifest(filename, withInstance(null, agent('sl', { connectors: ['lark', 'slack', 'telegram'] }))));
  assert.throws(() => validateManifest(filename, withInstance(null, { ...sl, model: 'ark',
    volcSessionId: 'session-test', volcEnvironmentId: 'env-test' })), /slack connector cannot use the ark runtime/);
});

test('Telegram can be primary or secondary; Ark and a stray Lark profile are rejected', (t) => {
  const home = tempHome(t);
  const filename = path.join(home, '.vika', 'config.json');
  const wa = agent('wa', { channel: 'telegram', connectors: ['telegram'], profile: undefined, model: 'pi' });
  assert.doesNotThrow(() => validateManifest(filename, withInstance(null, wa)));
  assert.doesNotThrow(() => validateManifest(filename, withInstance(null, agent('wa', { connectors: ['lark', 'telegram'] }))));
  assert.throws(() => validateManifest(filename, withInstance(null, { ...wa, profile: 'stray' })), /profile/);
  assert.throws(() => validateManifest(filename, withInstance(null, { ...wa, model: 'ark',
    volcSessionId: 'session-test', volcEnvironmentId: 'env-test' })), /telegram connector cannot use the ark runtime/);
});

test('QQ can be primary or secondary, and cannot select Ark or a Lark-only profile', (t) => {
  const filename = path.join(tempHome(t), '.vika', 'config.json');
  const qq = agent('qq', { channel: 'qq', connectors: ['qq'], profile: undefined, model: 'pi' });
  assert.doesNotThrow(() => validateManifest(filename, withInstance(null, qq)));
  assert.doesNotThrow(() => validateManifest(filename, withInstance(null, agent('qq', { connectors: ['lark', 'qq'] }))));
  assert.throws(() => validateManifest(filename, withInstance(null, { ...qq, profile: 'stray' })), /profile/);
  assert.throws(() => validateManifest(filename, withInstance(null, { ...qq, model: 'ark',
    volcSessionId: 'session-test', volcEnvironmentId: 'env-test' })), /qq connector cannot use the ark runtime/);
});
