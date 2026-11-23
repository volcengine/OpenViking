// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { AgentRuntime } from '../src/agent-runtime.mjs';
import { childConfig, connectorInstances, ConnectorProcess } from '../src/connectors/process.mjs';
import { assembleConversationPolicy } from '../src/conversation-policy.mjs';
import { ProcessRegistry } from '../src/process-utils.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

test('an external connector is its own program, listed beside built-in ones', () => {
  const instances = connectorInstances({
    connectors: ['lark', 'dingtalk'],
    externalConnectors: [{ id: 'dingtalk', command: '/usr/local/bin/ovsa-dingtalk', args: ['--once'] }],
  });
  assert.deepEqual(instances.find((item) => item.id === 'dingtalk'), {
    id: 'dingtalk', type: 'dingtalk', command: '/usr/local/bin/ovsa-dingtalk', args: ['--once'],
  });
  assert.deepEqual(instances.find((item) => item.id === 'lark'), { id: 'lark', type: 'lark' });
});

test('an external connector config carries no Lark profile or owner', () => {
  const config = childConfig({
    profile: 'superagent-sre', larkOwnerOpenId: 'ou_owner', larkCliBin: '/fake/lark-cli',
    projectDir: '/proj', dataDir: '/data', tempDir: '/tmp/superagent', logLevel: 'info',
  }, { id: 'dingtalk', type: 'dingtalk', command: '/usr/local/bin/ovsa-dingtalk', args: [] });
  assert.equal(config.channel, 'dingtalk');
  assert.equal(config.instance, 'dingtalk');
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
    id: 'dingtalk', type: 'dingtalk', command: process.execPath, args: [fixture, '--once'],
  }, {
    projectDir: dir, dataDir: dir, maxEventBytes: 1_000_000, logLevel: 'error',
    profile: 'should-not-leak', larkOwnerOpenId: 'ou_secret', larkCliBin: '/fake/lark-cli',
  }, registry, logger);
  t.after(async () => { await connector.stop(); await registry.stopAll(1000); });
  const spec = await connector.preflight();
  assert.equal(spec.manifest.name, 'dingtalk');
  assert.equal(spec.manifest.actions['message.send'].content.includes('markdown'), true);
  assert.equal(connector.owner.id, 'owner-1');
  assert.equal(connector.owner.target.chat_id, 'direct:1');
  const received = JSON.parse(fs.readFileSync(path.join(dir, 'received.json'), 'utf8'));
  assert.deepEqual(received.argv, [fixture, '--once']);
  assert.equal(received.config.channel, 'dingtalk');
  assert.equal(received.config.profile, undefined);
  assert.equal(received.config.larkOwnerOpenId, undefined);
  assert.equal(received.config.larkCliBin, undefined);
});

test('the model is told which external connector a line came from', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-external-prompt-'));
  const prompt = path.join(dir, 'dingtalk.md');
  fs.writeFileSync(prompt, 'Never invent a target on another connector.\n');
  try {
    const text = assembleConversationPolicy({
      connectors: ['lark', 'dingtalk'],
      externalConnectors: [{ id: 'dingtalk', command: '/usr/local/bin/ovsa-dingtalk', prompt }],
    });
    assert.match(text, /^## dingtalk$/m);
    assert.match(text, /source=dingtalk/);
    assert.match(text, /Never invent a target on another connector/);
    assert.ok(text.indexOf('## Lark') < text.indexOf('## dingtalk'));
    assert.ok(text.indexOf('## dingtalk') < text.indexOf('## Role'));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('Volc cannot run an external connector', () => {
  const runtime = Object.create(AgentRuntime.prototype);
  runtime.config = { connectors: ['dingtalk'], externalConnectors: [{ id: 'dingtalk' }] };
  assert.throws(() => runtime._assertConnectorSupport('volc'), /external connector/);
  runtime.config = { connectors: ['lark'], externalConnectors: [] };
  assert.doesNotThrow(() => runtime._assertConnectorSupport('volc'));
});
