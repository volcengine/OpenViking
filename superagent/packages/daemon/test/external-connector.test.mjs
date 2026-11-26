// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { ActionService } from '../src/actions/service.mjs';
import { AgentRuntime } from '../src/agent-runtime.mjs';
import { childConfig, connectorInstances, ConnectorProcess } from '../src/connectors/process.mjs';
import { assembleConversationPolicy } from '../src/conversation-policy.mjs';
import { Harness } from '../src/harness.mjs';
import { ProcessRegistry } from '../src/process-utils.mjs';
import { Store } from '../src/store.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

test('an external connector is its own program, listed beside built-in ones', () => {
  const instances = connectorInstances({
    connectors: ['lark', 'mattermost'],
    externalConnectors: [{ id: 'mattermost', command: '/usr/local/bin/ovsa-mattermost', args: ['--once'] }],
  });
  assert.deepEqual(instances.find((item) => item.id === 'mattermost'), {
    id: 'mattermost', type: 'mattermost', command: '/usr/local/bin/ovsa-mattermost', args: ['--once'],
  });
  assert.deepEqual(instances.find((item) => item.id === 'lark'), { id: 'lark', type: 'lark' });
});

test('an external connector config carries no Lark profile or owner', () => {
  const config = childConfig({
    profile: 'superagent-sre', larkOwnerOpenId: 'ou_owner', larkCliBin: '/fake/lark-cli',
    projectDir: '/proj', dataDir: '/data', tempDir: '/tmp/superagent', logLevel: 'info',
  }, { id: 'mattermost', type: 'mattermost', command: '/usr/local/bin/ovsa-mattermost', args: [] });
  assert.equal(config.channel, 'mattermost');
  assert.equal(config.instance, 'mattermost');
  assert.equal(config.profile, undefined);
  assert.equal(config.larkOwnerOpenId, undefined);
  assert.equal(config.larkCliBin, undefined);
});

test('the configured program speaks protocol 0.2 and names the owner', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-external-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const fixture = fileURLToPath(new URL('./external-connector-fixture.mjs', import.meta.url));
  const registry = new ProcessRegistry(logger);
  const connector = new ConnectorProcess({
    id: 'mattermost', type: 'mattermost', command: process.execPath, args: [fixture, '--once'],
  }, {
    projectDir: dir, dataDir: dir, maxEventBytes: 1_000_000, logLevel: 'error',
    profile: 'should-not-leak', larkOwnerOpenId: 'ou_secret', larkCliBin: '/fake/lark-cli',
  }, registry, logger);
  t.after(async () => { await connector.stop(); await registry.stopAll(1000); });
  const spec = await connector.preflight();
  assert.equal(spec.manifest.name, 'mattermost');
  assert.equal(spec.manifest.actions['message.send'].content.includes('markdown'), true);
  assert.equal(connector.owner.id, 'owner-1');
  assert.equal(connector.owner.target.chat_id, 'direct:1');
  const received = JSON.parse(fs.readFileSync(path.join(dir, 'received.json'), 'utf8'));
  assert.deepEqual(received.argv, [fixture, '--once']);
  assert.equal(received.config.channel, 'mattermost');
  assert.equal(received.config.profile, undefined);
  assert.equal(received.config.larkOwnerOpenId, undefined);
  assert.equal(received.config.larkCliBin, undefined);
});

test('the model is told which external connector a line came from', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-external-prompt-'));
  const prompt = path.join(dir, 'mattermost.md');
  fs.writeFileSync(prompt, 'Never invent a target on another connector.\n');
  try {
    const text = assembleConversationPolicy({
      connectors: ['lark', 'mattermost'],
      externalConnectors: [{ id: 'mattermost', command: '/usr/local/bin/ovsa-mattermost', prompt }],
    });
    assert.match(text, /^## mattermost$/m);
    assert.match(text, /source=mattermost/);
    assert.match(text, /Never invent a target on another connector/);
    assert.ok(text.indexOf('## Lark') < text.indexOf('## mattermost'));
    assert.ok(text.indexOf('## mattermost') < text.indexOf('## Role'));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('a missing external prompt is skipped instead of failing policy assembly', () => {
  const missing = path.join(os.tmpdir(), 'ovsa-missing-prompt.md');
  const warnings = [];
  const text = assembleConversationPolicy({
    connectors: ['mattermost'],
    externalConnectors: [{ id: 'mattermost', command: '/usr/local/bin/ovsa-mattermost', prompt: missing }],
  }, { logger: { warn: (message, fields) => warnings.push([message, fields]) } });
  assert.match(text, /^## mattermost$/m);
  assert.equal(text.includes(missing), false);
  assert.deepEqual(warnings, [['external_connector_prompt_unreadable', { connector: 'mattermost', code: 'ENOENT' }]]);
});

test('an external prompt shows at most 16384 characters', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-external-prompt-'));
  const prompt = path.join(dir, 'mattermost.md');
  fs.writeFileSync(prompt, `${'a'.repeat(16_384)}TAIL`);
  try {
    const text = assembleConversationPolicy({
      connectors: ['mattermost'],
      externalConnectors: [{ id: 'mattermost', command: '/usr/local/bin/ovsa-mattermost', prompt }],
    });
    assert.equal(text.includes('a'.repeat(16_384)), true);
    assert.equal(text.includes('TAIL'), false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('an inbound line wakes the model and the reply, then /help, is delivered back', async (t) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-external-e2e-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const fixture = fileURLToPath(new URL('./external-connector-fixture.mjs', import.meta.url));
  const registry = new ProcessRegistry(logger);
  const connector = new ConnectorProcess({
    id: 'mattermost', type: 'mattermost', command: process.execPath, args: [fixture],
  }, {
    projectDir: dir, dataDir: dir, maxEventBytes: 1_000_000, logLevel: 'error', sendTimeoutMs: 5_000,
  }, registry, logger);
  const store = new Store(path.join(dir, 'state.sqlite3'), logger);
  store.initializeCognition('codex', 'runtime-token');
  const connectors = {
    manifests: () => [['mattermost', connector.manifest]],
    invoke: (_id, action, input) => connector.invoke(action, input),
    available: () => connector.available,
  };
  const actions = new ActionService({
    config: { channel: 'mattermost', connectors: ['mattermost'], maxReplyChars: 4_000, maxEventBytes: 1_000_000, maxEventAgeMs: 60_000 },
    store, connectors, logger,
  });
  const delivered = [];
  const commands = [];
  let wake;
  let commanded;
  const woken = new Promise((resolve) => { wake = resolve; });
  const helped = new Promise((resolve) => { commanded = resolve; });
  const harness = new Harness({
    config: { maxEventBytes: 1_000_000, maxEventAgeMs: 60_000, ignoredSenderIds: new Set() },
    store, logger, recordActivity() {},
    runtime: {
      snapshot: () => ({ session: { connected: true }, work: { turnInProgress: false } }),
      submit: (items) => { delivered.push(...items); wake(); return { accepted: true }; },
    },
    onSlashCommand: async (command, event, inbound) => {
      commands.push(command.name);
      const sent = await actions.replyToEvent({ ...event, seq: inbound?.seq }, 'help text', 'command-reply');
      assert.equal(sent.ok, true);
      commanded();
    },
  });
  t.after(async () => {
    harness.stop();
    await connector.stop();
    await registry.stopAll(1000);
    store.close();
  });
  await connector.preflight();
  await connector.startSubscription(
    (event) => harness.handleConnectorEvent(event),
    () => assert.fail('unexpected connector exit'),
  );
  await woken;
  await harness.ingressBarrier;
  assert.equal(delivered.length, 1);
  assert.match(delivered[0].text, /hello/);
  await actions.call('reply_message', {
    message_id: 'dm-1', markdown: '回答', decision_id: 'message:dm-1',
  }, { authority: 'runtime-token' });
  await helped;
  await harness.ingressBarrier;
  assert.deepEqual(commands, ['help']);
  assert.equal(delivered.length, 1, 'a local /help does not wake the model');
  const sent = JSON.parse(fs.readFileSync(path.join(dir, 'actions.json'), 'utf8'));
  assert.equal(sent.length, 2);
  assert.equal(sent[0].name, 'message.reply');
  assert.equal(sent[0].input.message_id, 'dm-1');
  assert.equal(sent[0].input.content.text, '回答');
  assert.equal(sent[1].name, 'message.reply');
  assert.equal(sent[1].input.message_id, 'help-1');
  assert.equal(sent[1].input.content.text, 'help text');
  assert.deepEqual(store.db.prepare('SELECT status FROM outbox ORDER BY id').all().map((row) => row.status), ['SENT', 'SENT']);
  assert.deepEqual(
    store.db.prepare('SELECT event_id, status FROM connector_events ORDER BY seq').all().map((row) => ({
      event_id: row.event_id, status: row.status,
    })),
    [
      { event_id: 'message:dm-1', status: 'DONE' },
      { event_id: 'message:help-1', status: 'RULE_IGNORED' },
    ],
  );
});

test('Volc cannot run an external connector', () => {
  const runtime = Object.create(AgentRuntime.prototype);
  runtime.config = { connectors: ['mattermost'], externalConnectors: [{ id: 'mattermost' }] };
  assert.throws(() => runtime._assertConnectorSupport('volc'), /external connector/);
  runtime.config = { connectors: ['lark'], externalConnectors: [] };
  assert.doesNotThrow(() => runtime._assertConnectorSupport('volc'));
});
