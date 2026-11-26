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
import { DingTalkClient } from '../src/dingtalk/client.mjs';
import { DWS_ALL_GROUPS, DWS_EVENT, DwsSubscription } from '../src/dingtalk/dws.mjs';
import { DingTalkState } from '../src/dingtalk/state.mjs';
import { normalizeMessage, parseChatId, parseMessageTarget } from '../src/dingtalk/events.mjs';
import { invokeOperation } from '../src/connectors/operations.mjs';
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

function setupClient(t, registry = null) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ovsa-dingtalk-'));
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
  return { directory, client: new DingTalkClient({ dataDir: directory }, registry, logger), fetch, sends,
    beforeCleanup: (callback) => { close = callback; } };
}

for (const channel of ['stream', 'dws']) {
  test('DingTalk ' + channel + ' reaches Harness and sends replies and reminders to its group', async (t) => {
    let subscriber;
    const personalSends = [];
    const registry = { run: async (bin, args, options) => {
      if (args.includes('auth')) return { code: 0, stdout: '{"authenticated":true}' };
      assert.equal(args[args.indexOf('--group') + 1], groupId);
      assert.ok(args.includes('--idempotency-key'));
      personalSends.push({ input: options.input, command: args[3] });
      return { code: 0, stdout: JSON.stringify({ deliveryStatus: 'delivered', openMessageId: 'sent-' + personalSends.length }) };
    } };
    const { client, directory, fetch, sends, beforeCleanup } = setupClient(t, registry);
    if (channel === 'dws') {
      fs.writeFileSync(path.join(directory, 'dingtalk-policy.json'), JSON.stringify({
        dws: { profile: 'corp:user', tenantId: 'corp-fixture', allGroups: true, sendAs: 'user', userOpenId: 'user-self' },
      }));
      t.mock.method(DwsSubscription.prototype, 'start', function () { subscriber = this; });
    }
    const order = [];
    let stream;
    t.mock.method(DWClient.prototype, 'connect', function () {
      stream = this;
      this.connected = true;
    });
    t.mock.method(DWClient.prototype, 'disconnect', function () { this.connected = false; });
    t.mock.method(DWClient.prototype, 'socketCallBackResponse', (id) => order.push(id));
    assert.deepEqual(await client.preflight(), { self: { id: channel === 'dws' ? 'user-self' : robotCode } });
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
    client.startSubscription((line) => {
      event = JSON.parse(line);
      harness.handleConnectorEvent(event);
      order.push('EVENT');
    }, () => {});
    if (channel === 'stream') {
      assert.deepEqual(stream.getConfig().subscriptions, [{ type: 'CALLBACK', topic: TOPIC_ROBOT }]);
      stream.emit(TOPIC_ROBOT, callback());
      stream.emit(TOPIC_ROBOT, callback({ msgId: 'other', isInAtList: false }, 'ignored-delivery'));
      assert.deepEqual(order, ['stream-delivery', 'EVENT', 'ignored-delivery']);
    } else {
      const message = {
        type: DWS_ALL_GROUPS, conversation_id: groupId, message_id: 'dws-original',
        sender_open_dingtalk_id: 'another-user', event_time: Date.now(), content: 'Set a test reminder',
        quoted_message: { conversation_id: groupId, message_id: 'quoted', sender_open_dingtalk_id: 'user-self' },
      };
      await subscriber.onEvent(message, groupId);
    }
    await harness.ingressBarrier;
    assert.equal(inbound.length, 1);
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
    // The message id carries its group: the reply needs no lookup.
    await call('reply_message', { message_id: messageId, markdown: 'Timer created', decision_id: event.id });

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
    if (channel === 'stream') {
      assert.deepEqual(sends, ['Timer created', 'Reminder is due']);
      assert.equal(fetch.mock.callCount(), 3);
    } else {
      assert.deepEqual(personalSends, [
        { input: 'Timer created', command: '+messages-reply' },
        { input: 'Reminder is due', command: '+messages-send' },
      ]);
      assert.equal(fetch.mock.callCount(), 0);
    }
    await harness.ingressBarrier;
  });
}

test('DingTalk commits each message before ACK and hands it to the Core once, across restarts', async (t) => {
  const { client, directory } = setupClient(t);
  const pending = () => {
    const db = new DatabaseSync(path.join(directory, 'dingtalk/state.sqlite3'), { readOnly: true });
    try { return db.prepare('SELECT COUNT(*) AS count FROM pending').get().count; } finally { db.close(); }
  };
  let stream;
  t.mock.method(DWClient.prototype, 'connect', function () { stream = this; this.connected = true; });
  t.mock.method(DWClient.prototype, 'disconnect', function () { this.connected = false; });
  const acknowledged = [];
  t.mock.method(DWClient.prototype, 'socketCallBackResponse', (id) => acknowledged.push([id, pending()]));
  await client.preflight();
  // Staged by a worker that stopped before handing it over.
  const leftOver = normalizeMessage(callback({ msgId: 'left-over' }), { botId: robotCode });
  const state = new DingTalkState(directory);
  try { state.stage(leftOver); } finally { state.close(); }
  const lines = [];
  const start = () => client.startSubscription((line) => lines.push(JSON.parse(line).id), () => {});
  try {
    start();
    assert.deepEqual(lines, [leftOver.id]);
    stream.emit(TOPIC_ROBOT, callback());
    stream.emit(TOPIC_ROBOT, { headers: { messageId: 'malformed' }, data: '{bad' });
    // Committed when acknowledged; a malformed callback is acknowledged and dropped.
    assert.deepEqual(acknowledged, [['stream-delivery', 1], ['malformed', 0]]);
    assert.equal(lines.length, 2);
    client.stop();
    start();
    assert.equal(lines.length, 2);
  } finally { client.stop(); }
});

test('DingTalk reports a Stream that stays disconnected to the Core', async (t) => {
  const { client } = setupClient(t);
  t.mock.method(DWClient.prototype, 'connect', async () => {});
  t.mock.method(DWClient.prototype, 'disconnect', () => {});
  await client.preflight();
  t.mock.timers.enable({ apis: ['setInterval', 'Date'] });
  const exits = [];
  try {
    const exited = Promise.withResolvers();
    client.startSubscription(() => {}, (exit) => { exits.push(exit); exited.resolve(); });
    t.mock.timers.tick(55_000);
    assert.equal(exits.length, 0);
    t.mock.timers.tick(5_000);
    await exited.promise;
    assert.equal(exits.length, 1);
    assert.match(exits[0].stderr, /disconnected for 60 seconds/);
  } finally { client.stop(); }
});

test('the deployment manifest and connector worker recognize DingTalk as a secondary group connector', async (t) => {
  const require = createRequire(import.meta.url);
  const { loadDeploymentManifest, applyDeploymentAgentEnv } = require('../../../deployment-config.cjs');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ovsa-dingtalk-config-'));
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
    id: 'dingtalk', displayName: 'OVSA DingTalk', channel: 'lark', connectors: ['dingtalk', 'lark'],
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

test('DingTalk event ids are stable across redeliveries and message ids carry their group', () => {
  const original = callback({ senderNick: 'Fixture Sender' });
  const event = normalizeMessage(original, { botId: robotCode });
  const redelivery = { ...original, headers: { messageId: 'different-delivery' } };
  assert.equal(normalizeMessage(redelivery, { botId: robotCode }).id, event.id);
  assert.equal(parseChatId(event.conversation.id), groupId);
  assert.equal(parseMessageTarget(event.message.id).group, groupId);
  assert.equal(event.actor.name, 'Fixture Sender');
  assert.equal(normalizeMessage(callback({ conversationType: '1' }), { botId: robotCode }), null);
});

test('DingTalk routes each group through its chosen inbound channel while keeping robot replies', async (t) => {
  const registry = { run: async () => ({ code: 0, stdout: '{"authenticated":true}' }) };
  const { client, directory, sends, beforeCleanup } = setupClient(t, registry);
  const blockedGroup = 'disabled-group';
  const legacyGroup = 'legacy-stream-group';
  const robotOpenId = 'robot-open-id';
  fs.writeFileSync(path.join(directory, 'dingtalk-policy.json'), JSON.stringify({
    dws: { profile: 'corp:user', tenantId: 'corp-fixture', robotOpenId },
    groups: { [groupId]: { channel: 'dws', strategy: 'reply-only' }, [blockedGroup]: { channel: 'off' } },
  }));
  t.mock.method(DWClient.prototype, 'connect', function () { this.connected = true; });
  t.mock.method(DWClient.prototype, 'disconnect', function () { this.connected = false; });
  t.mock.method(DWClient.prototype, 'socketCallBackResponse', () => {});
  let subscriber;
  t.mock.method(DwsSubscription.prototype, 'start', function (groups) {
    assert.deepEqual(groups, [groupId]);
    subscriber = this;
  });
  await client.preflight();
  beforeCleanup(() => void client.stop());
  const events = [];
  client.startSubscription((line) => events.push(JSON.parse(line)), () => assert.fail('unexpected exit'));
  client.receive(callback());
  client.receive(callback({ conversationId: blockedGroup }));
  client.receive(callback({ conversationId: legacyGroup }));
  assert.deepEqual(events.map((event) => parseChatId(event.conversation.id)), [legacyGroup]);

  const message = {
    type: DWS_EVENT, conversation_id: groupId, message_id: 'first', sender_open_dingtalk_id: 'member',
    event_time: Date.now(), content: 'ordinary message',
  };
  subscriber.onEvent(message, legacyGroup);
  subscriber.onEvent({ ...message, conversation_id: blockedGroup }, groupId);
  await subscriber.onEvent(message, groupId);
  await subscriber.onEvent(message, groupId);
  assert.equal(events.length, 1);

  await subscriber.onEvent({ ...message, message_id: 'next', quoted_message: {
    conversation_id: groupId, message_id: 'prior', sender_open_dingtalk_id: robotOpenId,
  } }, groupId);
  assert.equal(events.length, 3);
  assert.equal(events[1].content.text, 'ordinary message');
  assert.equal(parseChatId(events[2].conversation.id), groupId);
  await client.replyMessage({ message_id: events[2].message.id, content: { text: 'reply in original group' } });
  assert.deepEqual(sends, ['reply in original group']);
});

test('DingTalk keeps the other channel active through DWS restart and Stream reconnect', async (t) => {
  const registry = { run: async () => assert.fail('mixed preflight must not gate Stream on DWS authorization') };
  const { client, directory, beforeCleanup } = setupClient(t, registry);
  fs.writeFileSync(path.join(directory, 'dingtalk-policy.json'), JSON.stringify({
    dws: { profile: 'corp:user', tenantId: 'corp-fixture', robotOpenId: 'robot-open-id' },
    groups: { [groupId]: { channel: 'dws', strategy: 'reply-only' } },
  }));
  t.mock.method(DWClient.prototype, 'connect', function () { this.connected = true; });
  t.mock.method(DWClient.prototype, 'disconnect', function () { this.connected = false; });
  const startDws = t.mock.method(DwsSubscription.prototype, 'start', () => {});
  await client.preflight();
  t.mock.timers.enable({ apis: ['setInterval', 'setTimeout', 'Date'] });
  beforeCleanup(() => void client.stop());
  const exited = Promise.withResolvers();
  client.startSubscription(() => {}, exited.resolve);

  const firstStream = client.stream;
  client.dws.fail('fixture DWS failure');
  await client.dws.stop();
  await Promise.resolve();
  assert.equal(client.stream, firstStream);
  assert.equal(firstStream.connected, true);
  t.mock.timers.tick(60_000);
  assert.equal(startDws.mock.callCount(), 2);

  const secondDws = client.dws;
  firstStream.connected = false;
  t.mock.timers.tick(5_000);
  t.mock.timers.tick(60_000);
  assert.equal(client.stream, firstStream);
  assert.equal(client.dws, secondDws);
  firstStream.connected = true;
  t.mock.timers.tick(5_000);
  assert.equal(client.stream.connected, true);
  assert.equal(client.dws, secondDws);

  firstStream.connected = false;
  t.mock.timers.tick(5_000);
  t.mock.timers.tick(60_000);
  t.mock.method(firstStream, 'disconnect', () => { throw new Error('fixture Stream cleanup failure'); });
  secondDws.fail('fixture both channels down');
  assert.match((await exited.promise).stderr, /both channels down/);
  assert.equal(secondDws.ended, true);
  assert.equal(client.state, null);
  const restartExit = Promise.withResolvers();
  client.startSubscription(() => {}, restartExit.resolve);
  assert.notEqual(client.dws, secondDws);
  assert.equal(client.stream.connected, true);
  const restartDws = client.dws;
  t.mock.method(restartDws, 'stop', async () => { throw new Error('fixture teardown timeout'); });
  restartDws.fail('fixture unconfirmed teardown');
  assert.match((await restartExit.promise).stderr, /fixture teardown timeout/);
  assert.equal(client.state, null);
  t.mock.timers.tick(60_000);
  assert.equal(startDws.mock.callCount(), 3);
});
