// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { PersonalAgentDaemon } from '../src/daemon.mjs';
import { ActionService } from '../src/actions/service.mjs';
import { Harness } from '../src/harness.mjs';
import { Store } from '../src/store.mjs';
import { ConnectorManager } from '../src/connectors/process.mjs';
import { writeManifest } from '../src/deployment-manifest.mjs';
import { executePrimaryCommand } from '../src/primary-channel.mjs';
import { fakeConnectors } from './action-fixture.mjs';
import { chatEvent, subjectEvent } from './event-fixture.mjs';

const require = createRequire(import.meta.url);
const { loadDeploymentManifest, applyDeploymentAgentEnv } = require('../../../deployment-config.cjs');
const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
const ids = ['lark', 'weixin', 'telegram', 'slack', 'qq'];

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-primary-'));
  const manifestPath = path.join(directory, 'custom-config.json');
  const agent = (id, port) => ({ id, displayName: id, channel: 'lark', connectors: ids,
    profile: `profile-${id}`, port, model: 'codex:', dataDir: path.join(directory, id, 'data'),
    workspace: path.join(directory, id, 'workspace'), ovConfig: path.join(directory, id, 'ov.json') });
  const first = agent('first', 3738), second = agent('second', 3739);
  const manifest = { version: 1, defaultInstance: 'first', env: { VIKA_LOG_LEVEL: 'warn' }, agents: [first, second] };
  writeManifest(manifestPath, manifest);
  fs.mkdirSync(second.workspace, { recursive: true });
  fs.mkdirSync(second.dataDir, { recursive: true });
  const daemon = new PersonalAgentDaemon({ ...second, instanceId: second.id, manifestPath,
    lockPath: path.join(second.dataDir, 'daemon.lock'), tempDir: path.join(second.dataDir, 'tmp'),
    maxReplyChars: 10_000, maxEventBytes: 100_000, maxEventAgeMs: 86_400_000 }, logger);
  const owners = Object.fromEntries(ids.map((id) => [id, { id: `${id}-owner`, target: { chat_id: `${id}-dm` } }]));
  const connectors = fakeConnectors({ ids, owners });
  const unavailable = new Set();
  connectors.available = (id) => !unavailable.has(id);
  connectors.isOwner = ConnectorManager.prototype.isOwner;
  daemon.connectors = connectors;
  const store = new Store(path.join(second.dataDir, 'state.sqlite3'), logger);
  store.initializeCognition('codex', 'test-authority');
  daemon.store = store;
  daemon.actions = new ActionService({ config: daemon.config, store, connectors, logger });
  // Even snapshot/whenIdle would couple this command to the model. None is needed.
  daemon.agentRuntime = new Proxy({}, { get: (_target, key) => assert.fail(`primary accessed runtime.${String(key)}`) });
  const harness = new Harness({ config: daemon.config, store, logger, recordActivity() {},
    runtime: daemon.agentRuntime, onSlashCommand: (...args) => daemon.handleSlashCommand(...args) });
  t.after(() => { store.close(); fs.rmSync(directory, { recursive: true, force: true }); });
  let sequence = 0;
  const receive = async (source, text, fields = {}) => {
    const event = chatEvent({ source, chatId: `${source}-dm`, senderId: `${source}-owner`,
      messageId: `command-${++sequence}`, content: text, ...fields });
    harness.handleConnectorEvent(event.envelope);
    await harness.ingressBarrier;
    const reply = connectors.calls.findLast(({ action, input }) => action === 'message.reply' && input.message_id === event.messageId);
    return { event, reply, text: reply?.input.content.text };
  };
  return { directory, manifestPath, manifest, daemon, store, connectors, owners, unavailable, harness, receive,
    saved: () => JSON.parse(fs.readFileSync(manifestPath, 'utf8')) };
}

test('every IM switches live through ingress/outbox and survive config reload without touching the runtime', async (t) => {
  const f = fixture(t);
  const initial = fs.readFileSync(f.manifestPath, 'utf8');
  assert.match((await f.receive('telegram', '/primary status')).text, /当前主 IM：飞书（lark）/);
  assert.equal(fs.readFileSync(f.manifestPath, 'utf8'), initial);
  for (const source of ['telegram', 'slack', 'qq', 'weixin', 'lark']) {
    const result = await f.receive(source, source === 'slack' ? ' /primary' : '/primary');
    assert.match(result.text, /立即生效，无需重启/);
    assert.equal(result.reply.connector, source);
    assert.equal(f.daemon.config.channel, source);
    assert.deepEqual(f.saved(), { ...f.manifest,
      agents: [f.manifest.agents[0], { ...f.manifest.agents[1], channel: source }] });
    const restarted = {};
    applyDeploymentAgentEnv(restarted, loadDeploymentManifest({ VIKA_CONFIG: f.manifestPath }), 'second');
    assert.equal(restarted.VIKA_CHANNEL, source);
    assert.match((await f.receive(source, '/primary STATUS')).text, new RegExp(`当前主 IM：.*（${source}）`));

    const notice = subjectEvent({ eventId: `notice-${source}` });
    f.store.ingest(notice);
    await f.daemon.actions.call('notify_owner', { markdown: 'notice after switching', decision_id: notice.eventId }, { authority: 'test-authority' });
    const sent = f.connectors.calls.at(-1);
    assert.equal(sent.connector, source);
    assert.equal(sent.input.chat_id, f.owners[source].target.chat_id);
  }
  assert.equal(fs.statSync(f.manifestPath).mode & 0o777, 0o600);
});

test('a repeated selection and a duplicate event do not rewrite the manifest or send another reply', async (t) => {
  const f = fixture(t);
  await f.receive('telegram', '/primary');
  const before = fs.statSync(f.manifestPath);
  const backup = fs.readFileSync(`${f.manifestPath}.bak`, 'utf8');
  const repeated = await f.receive('telegram', '/p');
  assert.match(repeated.text, /当前主 IM 已是Telegram/);
  assert.equal(fs.statSync(f.manifestPath).ino, before.ino);
  assert.equal(fs.readFileSync(`${f.manifestPath}.bak`, 'utf8'), backup);
  const calls = f.connectors.calls.length;
  f.harness.handleConnectorEvent(repeated.event.envelope);
  await f.harness.ingressBarrier;
  assert.equal(f.connectors.calls.length, calls);
});

test('the incoming IM owner can switch away from an unavailable primary', async (t) => {
  const f = fixture(t);
  f.unavailable.add('lark');
  assert.match((await f.receive('telegram', '/primary')).text, /立即生效/);
  assert.equal(f.daemon.config.channel, 'telegram');
});

test('switching patches the latest manifest and preserves changes to another instance and shared env', async (t) => {
  const f = fixture(t);
  const changed = f.saved();
  changed.agents[0].channel = 'slack';
  changed.agents[1].displayName = 'Renamed while running';
  changed.env.VIKA_LOG_LEVEL = 'error';
  writeManifest(f.manifestPath, changed);
  assert.match((await f.receive('telegram', '/primary')).text, /立即生效/);
  changed.agents[1].channel = 'telegram';
  assert.deepEqual(f.saved(), changed);
});

test('only the incoming IM owner can change or inspect the primary, including when a bot spoofs its id', async (t) => {
  const f = fixture(t);
  for (const command of ['/primary', '/primary status']) {
    for (const fields of [{ senderId: 'outsider' }, { senderId: 'lark-owner' }, { senderType: 'bot' }]) {
      assert.match((await f.receive('telegram', command, fields)).text, /仅限已绑定的 owner/);
    }
  }
  delete f.owners.telegram;
  assert.match((await f.receive('telegram', '/primary')).text, /仅限已绑定的 owner/);
  assert.deepEqual(f.saved(), f.manifest);
  assert.equal(f.daemon.config.channel, 'lark');
});

test('status works without a writable manifest, and extra arguments cannot switch another IM', async (t) => {
  const f = fixture(t);
  f.daemon.config.manifestPath = null;
  assert.match((await f.receive('telegram', '/primary status')).text, /当前主 IM：飞书/);
  for (const arg of ['slack', 'status slack', 'set', 'statu']) {
    assert.match((await f.receive('telegram', `/primary ${arg}`)).text, /用法/);
  }
  assert.match((await f.receive('telegram', '/primary')).text, /无法定位/);
  assert.deepEqual(f.saved(), f.manifest);
});

test('rejects unavailable, unsupported, unconfigured and unroutable targets without changing the primary', async (t) => {
  for (const [name, prepare, message] of [
    ['unavailable', (f) => f.unavailable.add('telegram'), /暂不可用/],
    ['unsupported', (f) => { f.connectors.manifests = () => [['telegram', { actions: {} }]]; }, /不支持作为主 IM/],
    ['unconfigured', (f) => { f.daemon.config.connectors = ['lark']; }, /不支持作为主 IM/],
    ['unroutable', (f) => { f.owners.telegram.target = {}; }, /缺少 owner 的私聊地址/],
  ]) await t.test(name, async (t) => {
    const f = fixture(t);
    prepare(f);
    // Exercise validation directly: a disconnected connector cannot deliver a rejection.
    const result = executePrimaryCommand({ arg: '' }, { event: chatEvent({ source: 'telegram', senderId: 'telegram-owner' }),
      config: f.daemon.config, connectors: f.connectors });
    assert.equal(result.ok, false);
    assert.match(result.text, message);
    assert.deepEqual(f.saved(), f.manifest);
    assert.equal(f.daemon.config.channel, 'lark');
  });
});

test('a failed write leaves the old live route and file intact and cleans up the lock and staged file', async (t) => {
  const f = fixture(t);
  fs.mkdirSync(`${f.manifestPath}.bak`); // Force backup failure even under a privileged test user.
  assert.match((await f.receive('telegram', '/primary')).text, /命令执行失败/);
  assert.equal(f.daemon.config.channel, 'lark');
  assert.deepEqual(f.saved(), f.manifest);
  assert.ok(!fs.existsSync(`${f.manifestPath}.lock`));
  assert.ok(!fs.readdirSync(f.directory).some((file) => file.endsWith('.tmp')));
});

test('refuses to overwrite stale instance configuration or a corrupt manifest', async (t) => {
  for (const [name, change] of [
    ['removed', (value) => { value.agents.pop(); }],
    ['disconnected', (value) => { value.agents[1].connectors = ['lark']; }],
    ['replaced', (value) => { value.agents[1].dataDir += '-replacement'; }],
    ['changed primary', (value) => { value.agents[1].channel = 'slack'; }],
  ]) await t.test(name, async (t) => {
    const f = fixture(t);
    const changed = f.saved();
    change(changed);
    writeManifest(f.manifestPath, changed);
    assert.match((await f.receive('telegram', '/primary')).text, /命令执行失败/);
    assert.deepEqual(f.saved(), changed);
    assert.equal(f.daemon.config.channel, 'lark');
  });
  const f = fixture(t);
  fs.writeFileSync(f.manifestPath, '{invalid');
  assert.match((await f.receive('telegram', '/primary')).text, /命令执行失败/);
  assert.equal(fs.readFileSync(f.manifestPath, 'utf8'), '{invalid');
  assert.equal(f.daemon.config.channel, 'lark');
});
