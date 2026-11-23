// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { DatabaseSync } from 'node:sqlite';
import { test } from 'node:test';
import { DWClient, TOPIC_ROBOT } from 'dingtalk-stream';
import { DingTalkClient } from '../src/connectors/dingtalk.mjs';
import { DingTalkState } from '../src/connectors/dingtalk-state.mjs';
import { normalizeDingTalkEvent } from '../src/connectors/dingtalk-events.mjs';
import { invokeOperation } from '../src/connectors/operations.mjs';
import { normalizeEvent } from '../src/event.mjs';
import { ConnectorProcess } from '../src/connectors/process.mjs';
import { ProcessRegistry } from '../src/process-utils.mjs';
import { ActionService } from '../src/actions/service.mjs';
import { Harness } from '../src/harness.mjs';
import { TimerConnector, TimerRepository } from '../src/timers.mjs';
import { Store } from '../src/store.mjs';
import { fakeConnectors } from './action-fixture.mjs';

const logger = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };
const robotCode = 'fixture-robot';
const groupId = 'cidFixture/+=';
const callback = (overrides = {}, deliveryId = 'stream-delivery') => ({
  headers: { messageId: deliveryId },
  data: JSON.stringify({
    msgId: 'msgFixture/+=', conversationId: groupId, conversationType: '2',
    chatbotCorpId: 'corp-fixture', senderStaffId: 'sender-fixture', createAt: Date.now(),
    msgtype: 'text', isInAtList: true, text: { content: 'Set a test reminder' },
    sessionWebhook: 'https://example.invalid/must-not-be-saved',
    ...overrides,
  }),
});

function setupClient(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-dingtalk-'));
  let close = () => {};
  t.after(() => {
    try { close(); } finally { fs.rmSync(directory, { recursive: true, force: true }); }
  });
  fs.writeFileSync(path.join(directory, 'dingtalk.json'), JSON.stringify({
    clientId: 'fixture-app', clientSecret: 'fixture-secret', robotCode,
  }));
  const sends = [];
  const fetch = t.mock.method(globalThis, 'fetch', async (url, request) => {
    const body = JSON.parse(request.body);
    if (url.endsWith('/oauth2/accessToken')) {
      assert.deepEqual(body, { appKey: 'fixture-app', appSecret: 'fixture-secret' });
      return Response.json({ accessToken: 'fixture-token', expireIn: 7200 });
    }
    assert.equal(url, 'https://api.dingtalk.com/v1.0/robot/groupMessages/send');
    assert.equal(request.headers['x-acs-dingtalk-access-token'], 'fixture-token');
    assert.equal(body.openConversationId, groupId);
    assert.equal(body.robotCode, robotCode);
    assert.equal(body.msgKey, 'sampleMarkdown');
    sends.push(JSON.parse(body.msgParam).text);
    return Response.json({ processQueryKey: 'fixture-delivery-receipt' });
  });
  return { directory, client: new DingTalkClient({ dataDir: directory }, null, logger), fetch, sends,
    beforeCleanup: (callback) => { close = callback; } };
}

test('DingTalk passes group mentions into Harness and routes replies and due timers through the group API', async (t) => {
  const { client, directory, fetch, sends, beforeCleanup } = setupClient(t);
  const order = [];
  let stream;
  t.mock.method(DWClient.prototype, 'connect', function () {
    stream = this;
    this.connected = true;
  });
  t.mock.method(DWClient.prototype, 'disconnect', function () { this.connected = false; });
  t.mock.method(DWClient.prototype, 'socketCallBackResponse', (id) => order.push(id));
  assert.deepEqual(await client.preflight(), { self: { id: robotCode } });
  const store = new Store(':memory:', logger);
  const token = 'test-runtime-token';
  store.initializeCognition('codex', token);
  const inbound = [];
  const harness = new Harness({
    config: { maxEventBytes: 100_000, maxEventAgeMs: 60_000 },
    store, logger, recordActivity() {},
    runtime: { snapshot: () => ({ work: {} }) },
    inbox: { usable: () => true, add: (item) => inbound.push(item), stop() {} },
  });
  beforeCleanup(() => { client.stop(); harness.stop(); store.close(); });
  let event;
  await client.startSubscription((line) => {
    event = JSON.parse(line);
    harness.handleConnectorEvent(event);
    order.push('EVENT');
  });
  assert.deepEqual(stream.getConfig().subscriptions, [{ type: 'CALLBACK', topic: TOPIC_ROBOT }]);
  stream.emit(TOPIC_ROBOT, callback());
  stream.emit(TOPIC_ROBOT, callback({ msgId: 'other', isInAtList: false }, 'ignored-delivery'));
  await harness.ingressBarrier;
  assert.equal(inbound.length, 1);
  assert.deepEqual(order, ['stream-delivery', 'EVENT', 'ignored-delivery']);
  assert.doesNotMatch(JSON.stringify(event), /must-not-be-saved/);
  const chatId = event.conversation.id;
  const messageId = event.message.id;
  assert.match(chatId, /^dtc_[\w-]+$/);

  let now = event.occurred_at;
  const timers = new TimerRepository(store.db);
  const service = new ActionService({
    config: { channel: 'lark', workspace: directory, tempDir: directory, maxReplyChars: 1000 },
    store, logger, timers, clock: () => now,
    connectors: fakeConnectors({ ids: ['lark', 'dingtalk'], run: (id, action, input) => {
      assert.equal(id, 'dingtalk');
      return invokeOperation(client, action, input);
    } }),
  });
  const call = (tool, input) => service.call(tool, input, { authority: token });
  const delivery = { mode: 'chat', source: 'dingtalk', chat_id: chatId };
  const timer = await call('create_timer', {
    title: 'Test', text: 'Reminder is due', decision_id: event.id, delivery,
    schedule: { type: 'once', run_at: new Date(now + 60_000).toISOString() },
  });
  assert.deepEqual(timer.data.delivery, delivery);
  await assert.rejects(call('reply_message', {
    source: 'dingtalk', message_id: 'dtm_dW5rbm93bg', markdown: 'Do not send', decision_id: event.id,
  }), /original message has no known chat/);
  store.ingest(normalizeEvent({
    ...event, connector: 'lark', instance: 'lark', id: 'message:om_foreign',
    subject: { type: 'message', id: 'om_foreign' },
    conversation: { ...event.conversation, id: 'oc_foreign' },
    message: { ...event.message, id: 'om_foreign' },
    reply_route: null,
  }));
  await assert.rejects(call('reply_message', {
    source: 'dingtalk', message_id: 'om_foreign', markdown: 'Do not send', decision_id: event.id,
  }), /original message has no known chat/);
  assert.deepEqual(sends, []);
  await call('reply_message', { source: 'dingtalk', message_id: messageId, markdown: 'Timer created', decision_id: event.id });

  now += 60_000;
  const submitted = [];
  const clock = new TimerConnector({ repository: timers, submitEvent: (due) => {
    harness.handleConnectorEvent(due);
    submitted.push(due);
  }, now: () => now, logger, autoStart: false });
  await clock.fireDueOnce();
  const [due] = submitted;
  assert.equal(due.kind, 'reminder.due');
  const occurrenceId = due.data.timer.occurrence_id;
  const send = { source: 'dingtalk', chat_id: chatId, markdown: 'Reminder is due', decision_id: due.id };
  await assert.rejects(call('finish_timer_occurrence', {
    occurrence_id: occurrenceId,
    delivery: { tool: 'send_message', input: { ...send, chat_id: 'dtc_d3Jvbmc' } },
  }), /configured for dingtalk chat/);
  const result = await call('finish_timer_occurrence', {
    occurrence_id: occurrenceId, delivery: { tool: 'send_message', input: send },
  });
  assert.equal(result.data.completed, true);
  const repeated = await call('finish_timer_occurrence', {
    occurrence_id: occurrenceId, delivery: { tool: 'send_message', input: send },
  });
  assert.equal(repeated.data.duplicate, true);
  assert.deepEqual(sends, ['Timer created', 'Reminder is due']);
  assert.equal(fetch.mock.callCount(), 3);
  await harness.ingressBarrier;
});

test('DingTalk persists before ACK and replays interrupted and completed handoffs', async (t) => {
  const { client, directory } = setupClient(t);
  let stream;
  const acknowledged = [];
  t.mock.method(DWClient.prototype, 'connect', function () {
    stream = this;
    this.connected = true;
  });
  t.mock.method(DWClient.prototype, 'disconnect', function () { this.connected = false; });
  t.mock.method(DWClient.prototype, 'socketCallBackResponse', (id) => {
    const db = new DatabaseSync(path.join(directory, 'dingtalk/receive.sqlite3'), { readOnly: true });
    try {
      assert.equal(db.prepare('SELECT COUNT(*) AS count FROM received').get().count, 1);
      assert.equal(db.prepare('SELECT forwarded_at FROM received').get().forwarded_at, null);
    } finally { db.close(); }
    acknowledged.push(id);
  });
  const restarted = new DingTalkClient({ dataDir: directory }, null, logger);
  const handoff = [];
  const replayed = [];
  try {
    await client.preflight();
    await client.startSubscription((line) => {
      handoff.push(JSON.parse(line));
      return new Promise(() => {});
    });
    stream.emit(TOPIC_ROBOT, callback());
    stream.emit(TOPIC_ROBOT, callback({}, 'redelivery'));
    assert.deepEqual(acknowledged, ['stream-delivery', 'redelivery']);
    assert.equal(handoff.length, 1);
    client.stop();

    await restarted.preflight();
    await restarted.startSubscription((line) => replayed.push(JSON.parse(line)));
    assert.deepEqual(replayed, handoff);
    restarted.stop();

    // Even a completed pipe handoff stays replayable within retention.
    await restarted.startSubscription((line) => replayed.push(JSON.parse(line)));
    assert.deepEqual(replayed, [...handoff, ...handoff]);
  } finally {
    client.stop();
    restarted.stop();
  }
});

test('DingTalk retries a failed handoff without new callbacks and cancels retry on stop', async (t) => {
  const { client } = setupClient(t);
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let stream;
  t.mock.method(DWClient.prototype, 'connect', function () { stream = this; this.connected = true; });
  t.mock.method(DWClient.prototype, 'disconnect', function () { this.connected = false; });
  const ack = t.mock.method(DWClient.prototype, 'socketCallBackResponse', () => {});
  const handoffs = [];
  try {
    await client.preflight();
    await client.startSubscription((line) => {
      handoffs.push(JSON.parse(line).id);
      if (handoffs.length !== 3) throw new Error('fixture pipe failure');
    });
    stream.emit(TOPIC_ROBOT, callback());
    assert.equal(ack.mock.callCount(), 1);
    assert.equal(handoffs.length, 1);
    t.mock.timers.tick(1_000);
    assert.equal(handoffs.length, 2);
    t.mock.timers.tick(1_999);
    assert.equal(handoffs.length, 2);
    t.mock.timers.tick(1);
    assert.deepEqual(handoffs, Array(3).fill(handoffs[0]));
    await Promise.resolve();
    stream.emit(TOPIC_ROBOT, callback({ msgId: 'second-message' }, 'second-delivery'));
    assert.equal(handoffs.length, 4);
    client.stop();
    t.mock.timers.tick(60_000);
    assert.equal(handoffs.length, 4);
  } finally { client.stop(); }
});

test('DingTalk leaves a callback unacknowledged when SQLite staging fails', async (t) => {
  const { client } = setupClient(t);
  let stream;
  t.mock.method(DWClient.prototype, 'connect', function () { stream = this; this.connected = true; });
  t.mock.method(DWClient.prototype, 'disconnect', function () { this.connected = false; });
  const ack = t.mock.method(DWClient.prototype, 'socketCallBackResponse', () => {});
  const stage = t.mock.method(DingTalkState.prototype, 'stage', () => { throw new Error('fixture disk failure'); });
  const handedOff = t.mock.fn();
  try {
    await client.preflight();
    await client.startSubscription(handedOff);
    stream.emit(TOPIC_ROBOT, callback());
    assert.equal(stage.mock.callCount(), 1);
    assert.equal(ack.mock.callCount(), 0);
    assert.equal(handedOff.mock.callCount(), 0);
  } finally { client.stop(); }
});

test('DingTalk acknowledges malformed callbacks and skips a corrupt journal row before forwarding valid events', async (t) => {
  const { client, directory } = setupClient(t);
  const account = JSON.stringify(['fixture-app', robotCode]);
  const state = new DingTalkState(directory, account);
  try { state.stage({ id: 'corrupt', type: 'REQUEST' }); } finally { state.close(); }
  let stream;
  t.mock.method(DWClient.prototype, 'connect', function () { stream = this; this.connected = true; });
  t.mock.method(DWClient.prototype, 'disconnect', function () { this.connected = false; });
  const ack = t.mock.method(DWClient.prototype, 'socketCallBackResponse', () => {});
  const handedOff = t.mock.fn();
  try {
    await client.preflight();
    await client.startSubscription(handedOff);
    stream.emit(TOPIC_ROBOT, { ...callback(), data: '{bad' });
    stream.emit(TOPIC_ROBOT, callback({ msgId: 'next-valid' }, 'next-delivery'));
    assert.deepEqual(ack.mock.calls.map(({ arguments: [id] }) => id), ['stream-delivery', 'next-delivery']);
    assert.equal(handedOff.mock.callCount(), 1);
    assert.equal(JSON.parse(handedOff.mock.calls[0].arguments[0]).type, 'EVENT');
    const inspect = new DingTalkState(directory, account);
    try {
      assert.equal(JSON.parse(inspect.next(0).envelope).type, 'EVENT');
    } finally { inspect.close(); }
  } finally { client.stop(); }
});

test('the deployment manifest and connector worker recognize DingTalk as a secondary group connector', async (t) => {
  const require = createRequire(import.meta.url);
  const { loadDeploymentManifest, applyDeploymentAgentEnv } = require('../../../deployment-config.cjs');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-dingtalk-config-'));
  const manifest = path.join(directory, 'config.json');
  const env = { OV_SUPERAGENT_CONFIG: manifest };
  const connector = new ConnectorProcess('dingtalk', {
    projectDir: directory, dataDir: directory, logLevel: 'error',
  }, new ProcessRegistry(logger), logger);
  t.after(async () => {
    await connector.stop();
    fs.rmSync(directory, { recursive: true, force: true });
  });
  const instance = {
    id: 'dingtalk', displayName: 'Vika DingTalk', channel: 'lark', connectors: ['dingtalk', 'lark'],
    profile: 'superagent-dingtalk-test',
    model: 'codex', port: 3839, dataDir: path.join(directory, 'data'), workspace: path.join(directory, 'workspace'),
    ovConfig: path.join(directory, 'ov.json'),
  };
  const writeManifest = () => fs.writeFileSync(manifest, JSON.stringify({
    version: 1, defaultInstance: 'dingtalk', env: {}, agents: [instance],
  }));
  instance.channel = 'dingtalk';
  writeManifest();
  assert.throws(() => loadDeploymentManifest(env), /requires an owner-capable IM/);
  delete instance.channel;
  writeManifest();
  applyDeploymentAgentEnv(env, loadDeploymentManifest(env));
  assert.equal(env.OV_SUPERAGENT_CHANNEL, 'lark');
  assert.deepEqual(JSON.parse(env.OV_SUPERAGENT_CONNECTORS_JSON), ['dingtalk', 'lark']);
  await assert.rejects(connector.preflight(), /Configure DingTalk app credentials/);
  assert.equal(connector.manifest.name, 'dingtalk');
});

test('DingTalk rejects a failed Stream start and keeps provider transport ids out of event identity', async (t) => {
  const { client } = setupClient(t);
  t.mock.method(DWClient.prototype, 'connect', async () => {});
  t.mock.method(DWClient.prototype, 'disconnect', () => {});
  await client.preflight();
  await assert.rejects(client.startSubscription(() => {}), /Could not start DingTalk Stream/);
  const original = callback();
  const redelivery = { ...original, headers: { messageId: 'different-delivery' } };
  assert.equal(normalizeDingTalkEvent(original, { botId: robotCode }).id,
    normalizeDingTalkEvent(redelivery, { botId: robotCode }).id);
  assert.equal(normalizeDingTalkEvent(callback({ conversationType: '1' }), { botId: robotCode }), null);
});
