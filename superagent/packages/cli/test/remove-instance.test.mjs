// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { removeManagedApps } from '../src/control.mjs';
import { readManifest, writeManifest } from '../src/manifest.mjs';
import { removeInstance } from '../src/remove-instance.mjs';

function fixture(t, ids = ['alpha', 'beta']) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-remove-'));
  t.after(() => fs.rmSync(home, { force: true, recursive: true }));
  const env = { HOME: home, PATH: '' };
  const configPath = path.join(home, '.vika', 'config.json');
  const agents = ids.map((id, index) => ({
    id, displayName: id.toUpperCase(), channel: 'weixin', connectors: ['weixin'], model: 'codex:', port: 3738 + index,
    dataDir: path.join(home, '.vika', 'data', id), workspace: path.join(home, 'workspace', id),
    ovConfig: path.join(home, '.vika', 'ov', `${id}.json`),
  }));
  const manifest = { version: 1, defaultInstance: ids[0], env: { CODEX_BIN: '/opt/codex' }, agents };
  writeManifest(configPath, manifest);
  const keptFiles = agents.flatMap((agent) => [path.join(agent.dataDir, 'state.sqlite3'), path.join(agent.workspace, 'notes'), agent.ovConfig]);
  for (const file of keptFiles) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, 'keep');
  }
  const calls = [];
  const output = [];
  const options = {
    manifest, configPath, env,
    prompter: {
      select: async () => agents[0],
      confirm: async (_question, initial) => { assert.equal(initial, false); return true; },
      info: (line) => output.push(line), success: (line) => output.push(line),
    },
    control: {
      remove: async (targets) => { calls.push(['remove', targets]); },
      restart: async (target) => { calls.push(['restart', target]); },
    },
  };
  return { home, options, calls, output, keptFiles, lock: path.join(home, '.vika', 'deploy.lock') };
}

test('removing the default selects a remaining instance and preserves all files and shared env', async (t) => {
  const f = fixture(t);
  assert.equal(await removeInstance(f.options), 0);
  const next = readManifest(f.options.env).value;
  assert.equal(next.defaultInstance, 'beta');
  assert.deepEqual(next.agents, [f.options.manifest.agents[1]]);
  assert.deepEqual(next.env, f.options.manifest.env);
  assert.deepEqual(f.calls, [['remove', ['alpha']], ['restart', 'web']]);
  assert.deepEqual(JSON.parse(fs.readFileSync(`${f.options.configPath}.bak`)), f.options.manifest);
  for (const file of f.keptFiles) assert.equal(fs.readFileSync(file, 'utf8'), 'keep');
  assert.equal(fs.existsSync(f.lock), false);
});

test('a default replacement can be chosen; removing another instance keeps the current default', async (t) => {
  const f = fixture(t, ['alpha', 'beta', 'gamma']);
  let questions = 0;
  f.options.prompter.select = async () => ++questions === 1 ? f.options.manifest.agents[0] : 'gamma';
  assert.equal(await removeInstance(f.options), 0);
  assert.equal(readManifest(f.options.env).value.defaultInstance, 'gamma');
  assert.equal(questions, 2);
  const g = fixture(t);
  g.options.prompter.select = async () => g.options.manifest.agents[1];
  assert.equal(await removeInstance(g.options), 0);
  assert.equal(readManifest(g.options.env).value.defaultInstance, 'alpha');
});

test('last removal stops shared services, backs up the manifest and returns to first setup', async (t) => {
  const f = fixture(t, ['alpha']);
  assert.equal(await removeInstance(f.options), 0);
  assert.deepEqual(f.calls, [['remove', ['updater', 'alpha', 'web']]]);
  assert.equal(readManifest(f.options.env).value, null);
  assert.deepEqual(JSON.parse(fs.readFileSync(`${f.options.configPath}.bak`)), f.options.manifest);
  for (const file of f.keptFiles) assert.equal(fs.readFileSync(file, 'utf8'), 'keep');
});

test('declining, a busy deploy and a concurrently edited manifest cause no process changes', async (t) => {
  for (const situation of ['decline', 'lock', 'edit']) {
    const f = fixture(t);
    if (situation === 'decline') f.options.prompter.confirm = async () => false;
    if (situation === 'lock') fs.mkdirSync(f.lock);
    if (situation === 'edit') writeManifest(f.options.configPath, { ...f.options.manifest, defaultInstance: 'beta' });
    const before = fs.readFileSync(f.options.configPath, 'utf8');
    if (situation === 'lock') await assert.rejects(removeInstance(f.options), /正在部署/);
    else assert.equal(await removeInstance(f.options), situation === 'decline' ? 0 : 1);
    assert.deepEqual(f.calls, []);
    assert.equal(fs.readFileSync(f.options.configPath, 'utf8'), before);
    assert.equal(fs.existsSync(f.lock), situation === 'lock');
  }
});

test('process/save failures retain the manifest for retry; Web failure reports removal already saved', async (t) => {
  const f = fixture(t);
  f.options.control.remove = async () => { throw new Error('pm2 save failed'); };
  assert.equal(await removeInstance(f.options), 1);
  assert.deepEqual(readManifest(f.options.env).value, f.options.manifest);
  assert.match(f.output.join('\n'), /删除未完成/);
  assert.equal(fs.existsSync(f.lock), false);
  const g = fixture(t);
  g.options.control.restart = async () => { throw new Error('web failed'); };
  assert.equal(await removeInstance(g.options), 1);
  assert.deepEqual(readManifest(g.options.env).value.agents.map(({ id }) => id), ['beta']);
  assert.match(g.output.join('\n'), /实例已删除，但控制台更新失败[\s\S]*restart web/);
  assert.equal(fs.existsSync(g.lock), false);
});

function managedFixture(t) {
  const f = fixture(t, ['alpha']);
  const releaseLink = path.join(f.home, '.vika', 'current');
  const release = path.join(f.home, '.vika', 'releases', 'older');
  const processes = ['alpha', 'web', 'updater'].map((id, pm_id) => {
    const component = id === 'alpha' ? 'daemon' : id;
    const script = path.join(release, 'packages', component, 'src', 'main.mjs');
    fs.mkdirSync(path.dirname(script), { recursive: true });
    fs.writeFileSync(script, '');
    return { name: `vika-${id}`, pm_id, pm2_env: {
      status: 'stopped', pm_exec_path: script, VIKA_CONFIG: f.options.configPath, VIKA_INSTANCE_ID: id,
    } };
  });
  const commands = [];
  const options = {
    env: f.options.env, releaseLink, pm2Bin: 'pm2',
    execFileImpl: async (_bin, args) => {
      commands.push(args);
      return { stdout: args[0] === 'jlist' ? JSON.stringify(processes) : '' };
    },
  };
  return { ...f, processes, commands, controlOptions: options };
}

test('managed removal accepts older releases and stopped apps, deletes exact ids and saves an empty list', async (t) => {
  const f = managedFixture(t);
  await removeManagedApps(['updater', 'alpha', 'web'], f.controlOptions);
  assert.deepEqual(f.commands, [['jlist'], ['delete', '2'], ['delete', '0'], ['delete', '1'], ['save', '--force']]);
  f.processes.length = 0;
  f.commands.length = 0;
  await removeManagedApps(['alpha'], f.controlOptions);
  assert.deepEqual(f.commands, [['jlist'], ['save', '--force']]);
});

test('all processes are checked before removal: duplicates, foreign scripts and manifests are refused', async (t) => {
  for (const invalid of ['duplicate', 'outside', 'wrong-script', 'other-manifest', 'other-instance', 'no-id', 'symlink']) {
    const f = managedFixture(t);
    const daemon = f.processes[0];
    if (invalid === 'duplicate') f.processes.push({ ...daemon, pm_id: 3 });
    if (invalid === 'outside') daemon.pm2_env.pm_exec_path = f.options.configPath;
    if (invalid === 'wrong-script') daemon.pm2_env.pm_exec_path = f.processes[1].pm2_env.pm_exec_path;
    if (invalid === 'other-manifest') {
      daemon.pm2_env.VIKA_CONFIG = `${f.options.configPath}.other`;
      fs.writeFileSync(daemon.pm2_env.VIKA_CONFIG, '{}');
    }
    if (invalid === 'no-id') delete daemon.pm_id;
    if (invalid === 'other-instance') daemon.pm2_env.VIKA_INSTANCE_ID = 'beta';
    if (invalid === 'symlink') {
      fs.unlinkSync(daemon.pm2_env.pm_exec_path);
      fs.symlinkSync(f.options.configPath, daemon.pm2_env.pm_exec_path);
    }
    await assert.rejects(removeManagedApps(['updater', 'alpha', 'web'], f.controlOptions));
    assert.deepEqual(f.commands, [['jlist']], invalid);
  }
});

test('a foreign Web is refused before deleting an instance; a managed Web is checked without removal', async (t) => {
  const f = managedFixture(t);
  f.processes[1].pm2_env.pm_exec_path = f.options.configPath;
  await assert.rejects(removeManagedApps(['alpha'], { ...f.controlOptions, checkTargets: ['web'] }), /refusing to remove/);
  assert.deepEqual(f.commands, [['jlist']]);
  const g = managedFixture(t);
  await removeManagedApps(['alpha'], { ...g.controlOptions, checkTargets: ['web'] });
  assert.deepEqual(g.commands, [['jlist'], ['delete', '0'], ['save', '--force']]);
});

test('a PM2 save failure keeps the manifest and can be retried after the process is already gone', async (t) => {
  const f = managedFixture(t);
  let failSave = true;
  f.controlOptions.execFileImpl = async (_bin, args) => {
    if (args[0] === 'jlist') return { stdout: JSON.stringify(f.processes) };
    if (args[0] === 'delete') f.processes.splice(f.processes.findIndex(({ pm_id }) => String(pm_id) === args[1]), 1);
    if (args[0] === 'save' && failSave) throw new Error('cannot write dump');
    return { stdout: '' };
  };
  f.options.control.remove = (targets, options) => removeManagedApps(targets, { ...options, ...f.controlOptions });
  assert.equal(await removeInstance(f.options), 1);
  assert.deepEqual(readManifest(f.options.env).value, f.options.manifest);
  assert.equal(f.processes.length, 0);
  failSave = false;
  assert.equal(await removeInstance(f.options), 0);
  assert.equal(readManifest(f.options.env).value, null);
});

test('a failed manifest backup leaves the original configuration intact and releases the lock', async (t) => {
  for (const ids of [['alpha'], ['alpha', 'beta']]) {
    const f = fixture(t, ids);
    fs.mkdirSync(`${f.options.configPath}.bak`);
    assert.equal(await removeInstance(f.options), 1);
    assert.deepEqual(readManifest(f.options.env).value, f.options.manifest);
    assert.equal(fs.existsSync(f.lock), false);
    assert.match(f.output.join('\n'), /删除未完成/);
  }
});
