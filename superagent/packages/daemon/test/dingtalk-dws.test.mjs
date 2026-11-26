// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { once } from 'node:events';
import { test } from 'node:test';
import { checkDws, DWS_ALL_GROUPS, DWS_EVENT, DwsSubscription, sendDws } from '../src/dingtalk/dws.mjs';
import { chatId, normalizeDwsMessage, normalizeMessage, parseMessageTarget } from '../src/dingtalk/events.mjs';
import { DingTalkGate } from '../src/dingtalk/gate.mjs';
import { parsePolicy } from '../src/dingtalk/policy.mjs';
import { DingTalkClient } from '../src/dingtalk/client.mjs';
import { DingTalkState } from '../src/dingtalk/state.mjs';
import { ProcessRegistry } from '../src/process-utils.mjs';
import { spawn } from '../src/platform/index.mjs';

const config = { cliBin: 'dws', profile: 'corp:user', tenantId: 'corp', robotOpenId: 'robot-open-id', selfOpenId: 'robot-open-id' };
const group = 'cidFixture/+=';
const fixture = {
  type: DWS_EVENT, conversation_id: group, message_id: 'msgFixture/+=',
  sender_open_dingtalk_id: 'sender-open-id', sender: 'member', content: '@Robot hello',
  event_time: 1_795_593_200_000,
};

test('DingTalk policy keeps default Stream and validates each explicit group route', () => {
  const policy = parsePolicy({ dws: config, groups: { [group]: { channel: 'dws', strategy: 'ambient' }, disabled: { channel: 'off' }, quoted: { channel: 'dws', strategy: 'reply-only' } } });
  assert.deepEqual(policy.dwsGroups, [group, 'quoted']);
  assert.equal(policy.route('quoted').strategy, 'reply-only');
  assert.equal(policy.route('legacy').channel, 'stream');
  assert.equal(policy.route('disabled').channel, 'off');
  assert.throws(() => parsePolicy({ groups: { invalid: { channel: 'stream', strategy: 'ambient' } } }));
  assert.throws(() => parsePolicy({ groups: { invalid: { channel: 'off', strategy: 'mention' } } }));
  assert.throws(() => parsePolicy({ groups: { invalid: { channel: 'dws', strategy: 'mention' } } }),
    /strategy "mention" was renamed to "reply-only"/);
  assert.throws(() => parsePolicy({ groups: { invalid: { channel: 'stream', strategy: 'reply-only' } } }),
    /Invalid DingTalk group channel or strategy/);
  assert.throws(() => parsePolicy({ defaultChannel: 'off' }), /requires an enabled/);
  assert.throws(() => parsePolicy({ groups: { invalid: { channel: ['stream'], strategy: 'mention' } } }), /Invalid DingTalk group/);
});

test('DWS auth uses the selected profile and the CLI authenticated status', async () => {
  const registry = { run: async (bin, args) => {
    assert.equal(bin, config.cliBin);
    assert.deepEqual(args, ['--profile', config.profile, 'auth', 'status', '--format', 'json']);
    return { code: 0, stdout: '{"authenticated":true}' };
  } };
  await checkDws(registry, config);
  await assert.rejects(checkDws({ run: async () => ({ code: 0, stdout: '{"authenticated":false}' }) }, config), /unauthorized/);
});

test('DWS transport accepts idle groups and reports a sustained source failure', async (t) => {
  t.mock.timers.enable({ apis: ['Date'] });
  const live = {
    bus: { pid: 42 }, source_state: { state: 'reconnecting' },
    consumers: [{ pid: 10, subscribe_id: 'expected' }],
  };
  const registry = { run: async (_bin, args) => {
    assert.deepEqual(args, ['--profile', config.profile, 'event', 'status', '--event', DWS_EVENT, '--format', 'json']);
    return { code: 0, stdout: JSON.stringify({ bus: { live } }) };
  } };
  const dws = new DwsSubscription(registry, config, { debug() {} }, () => {}, () => {});
  dws.children.set({ pid: 10 }, { subscription: { busPID: 42, id: 'expected' } });
  const failed = t.mock.method(dws, 'fail', () => {});
  await dws.checkTransport();
  t.mock.timers.tick(30_000);
  live.source_state.state = 'idle';
  await dws.checkTransport();
  t.mock.timers.tick(60_000);
  await dws.checkTransport();
  assert.equal(failed.mock.callCount(), 0);

  delete live.bus;
  await dws.checkTransport();
  t.mock.timers.tick(60_000);
  await dws.checkTransport();
  assert.equal(failed.mock.callCount(), 1);
});

test('DWS group events preserve replies and match Stream IDs when platform IDs match', () => {
  const quote = { conversation_id: group, message_id: 'quoted', sender_open_dingtalk_id: config.robotOpenId, content: 'prior response' };
  const event = normalizeDwsMessage({ ...fixture, quoted_message: quote }, { ...config, group });
  const stream = normalizeMessage({ data: JSON.stringify({
    conversationId: group, conversationType: '2', msgId: fixture.message_id, msgtype: 'text', createAt: fixture.event_time,
    chatbotCorpId: config.tenantId, senderId: 'member', text: { content: 'hello' },
  }) }, { botId: 'robot-code' });
  assert.equal(event.id, stream.id);
  assert.deepEqual(event.conversation, stream.conversation);
  assert.equal(parseMessageTarget(event.reply_route.input.message_id).group, group);
  assert.equal(parseMessageTarget(event.message.parent_id).group, group);
  assert.deepEqual(event.data, { quoted_sender_id: config.robotOpenId });
  assert.deepEqual(event.message.mentions, []);
  assert.equal(normalizeDwsMessage(fixture, { ...config, group: 'another' }), null);
  const unrelated = normalizeDwsMessage({ ...fixture, quoted_message: { ...quote, conversation_id: 'another' } }, { ...config, group });
  assert.equal(unrelated.message.parent_id, null);
});

test('DWS gate releases context on a direct event and stored ambient becomes context after a policy change', async (t) => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'ovsa-dingtalk-dws-'));
  const state = new DingTalkState(directory, config.profile);
  const received = [];
  const failures = [];
  const gate = new DingTalkGate(state, (key) => {
    for (const { seq, envelope } of state.pendingGroup(key)) {
      received.push(JSON.parse(envelope).id);
      state.delivered(seq);
    }
  }, (error) => failures.push(error.message));
  t.after(async () => { gate.stop(); state.close(); await rm(directory, { recursive: true, force: true }); });
  gate.stage({ id: 'context' }, group, 'context');
  gate.stage({ id: 'context' }, group, 'context');
  assert.deepEqual(received, []);
  gate.stage({ id: 'direct' }, group, 'direct');
  assert.deepEqual(received, ['context', 'direct']);
  state.stage({ id: 'waiting' }, group, 'ambient');
  state.reconcileGroup(group, { channel: 'dws', strategy: 'reply-only' });
  assert.equal(state.pendingGroup(group)[0].kind, 'context');
  state.reconcileGroup(group, { channel: 'stream', strategy: 'mention' });
  assert.deepEqual(state.pendingGroup(group), []);

  const query = t.mock.method(state, 'pendingGroup', () => { throw new Error('fixture journal unavailable'); });
  gate.stage({ id: 'recover-after-restart' }, group, 'direct');
  assert.deepEqual(failures, ['fixture journal unavailable']);
  query.mock.restore();
  assert.equal(state.pendingGroup(group).length, 1);
});

test('DWS pending messages survive a same-Profile restart and are discarded on Profile change', async (t) => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'ovsa-dingtalk-profile-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const original = new DingTalkState(directory, config.profile);
  original.stage({ id: 'stream' });
  original.stage({ id: 'dws' }, group, 'ambient');
  original.close();

  const restarted = new DingTalkState(directory, config.profile);
  assert.equal(restarted.pendingGroup(group).length, 1);
  restarted.close();

  const rotated = new DingTalkState(directory, 'corp:new-user');
  try {
    assert.deepEqual(rotated.pendingGroup(group), []);
    assert.equal(rotated.pending().length, 1);
    assert.equal(rotated.stage({ id: 'dws' }, group, 'ambient'), true);
  } finally {
    rotated.close();
  }
});

test('DWS can use the personal identity in every visible group without starting robot Stream', async (t) => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'ovsa-dingtalk-personal-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const userOpenId = 'personal-open-id';
  const groupPolicy = { dws: { ...config, allGroups: true, sendAs: 'user', userOpenId },
    groups: { disabled: { channel: 'off' } } };
  const policy = parsePolicy(groupPolicy);
  assert.equal(policy.stream, false);
  assert.equal(policy.route(group).strategy, 'ambient');
  assert.equal(policy.route('disabled').channel, 'off');
  assert.equal(policy.dws.selfOpenId, userOpenId);
  const event = normalizeDwsMessage({ ...fixture, type: DWS_ALL_GROUPS }, { ...policy.dws, group });
  assert.equal(parseMessageTarget(event.message.id).message, fixture.message_id);
  await writeFile(path.join(directory, 'dingtalk-policy.json'), JSON.stringify(groupPolicy));
  const calls = [];
  const bodies = [];
  const registry = { run: async (bin, args, options) => {
    calls.push(args);
    bodies.push(options.input);
    const data = args.includes('auth') ? { authenticated: true } :
      { deliveryStatus: 'delivered', openMessageId: 'a-dws-receipt' };
    return { code: 0, stdout: JSON.stringify(data) };
  } };
  const client = new DingTalkClient({ dataDir: directory }, registry, null);
  assert.deepEqual(await client.preflight(), { self: { id: userOpenId } });
  const reply = await client.replyMessage({ message_id: event.message.id, content: { text: 'reply' }, idempotency_key: 'core-reply-key' });
  assert.deepEqual(parseMessageTarget(reply.message_id), { group, message: 'a-dws-receipt' });
  assert.deepEqual(calls[1], ['--profile', config.profile, 'chat', '+messages-reply', '--as', 'user', '--group', group,
    '--message-id', fixture.message_id, '--content', '-', '--body-stdin', '--idempotency-key', 'core-reply-key', '--wait-delivery', '--yes', '--format', 'json']);
  await client.sendMessage({ chat_id: chatId(group), content: { text: '--help' }, idempotency_key: 'core-send-key' });
  assert.deepEqual(calls[2], ['--profile', config.profile, 'chat', '+messages-send', '--as', 'user', '--group', group,
    '--markdown', '-', '--body-stdin', '--idempotency-key', 'core-send-key', '--wait-delivery', '--yes', '--format', 'json']);
  await assert.rejects(client.sendMessage({ chat_id: chatId('disabled'), content: { text: 'blocked' } }), /disabled/);
  assert.equal(calls.length, 3);
  assert.deepEqual(bodies.slice(1), ['reply', '--help']);
});

test('DWS keeps same-group order around a pending send while another group can release', async (t) => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'ovsa-dingtalk-echo-'));
  const userOpenId = 'personal-open-id';
  const otherGroup = 'second-test-group';
  await writeFile(path.join(directory, 'dingtalk-policy.json'), JSON.stringify({
    defaultChannel: 'off', groups: {
      [group]: { channel: 'dws', strategy: 'ambient' }, [otherGroup]: { channel: 'dws', strategy: 'reply-only' },
    },
    dws: { ...config, sendAs: 'user', userOpenId },
  }));
  const receipt = Promise.withResolvers();
  const registry = { run: (_bin, args) => args.includes('auth')
    ? Promise.resolve({ code: 0, stdout: '{"authenticated":true}' }) : receipt.promise };
  t.mock.method(DwsSubscription.prototype, 'start', () => {});
  const warnings = [];
  const client = new DingTalkClient({ dataDir: directory }, registry, { warn: (event) => warnings.push(event) });
  t.after(async () => { await client.stop(); await rm(directory, { recursive: true, force: true }); });
  await client.preflight();
  const received = [];
  client.startSubscription((line) => received.push(JSON.parse(line).content.text), () => assert.fail('unexpected exit'));

  const sent = client.sendMessage({ chat_id: chatId(group), content: { text: 'agent' }, idempotency_key: 'key' });
  const own = client.receiveDws({ ...fixture, message_id: 'agent-id', sender_open_dingtalk_id: userOpenId, content: 'agent' }, group);
  const manual = client.receiveDws({ ...fixture, message_id: 'typed', sender_open_dingtalk_id: userOpenId, content: 'manual' }, group);
  const direct = client.receiveDws({ ...fixture, content: 'quoted', quoted_message: {
    conversation_id: group, message_id: 'agent-id', sender_open_dingtalk_id: userOpenId,
  } }, group);
  await client.receiveDws({ ...fixture, conversation_id: otherGroup, content: 'other group', quoted_message: {
    conversation_id: otherGroup, message_id: 'other-id', sender_open_dingtalk_id: userOpenId,
  } }, otherGroup);
  assert.deepEqual(received, ['other group']);
  receipt.resolve({ code: 0, stdout: '{"deliveryStatus":"delivered","openMessageId":"agent-id"}' });
  await Promise.all([sent, own, manual, direct]);
  assert.deepEqual(received, ['other group', 'manual', 'quoted']);

  t.mock.method(client.state, 'recordDwsSent', () => { throw new Error('fixture SQLite failure'); });
  t.mock.method(client.gate, 'sent', () => { throw new Error('fixture gate failure'); });
  const confirmed = await client.sendMessage({ chat_id: chatId(group), content: { text: 'confirmed' }, idempotency_key: 'another-key' });
  assert.equal(parseMessageTarget(confirmed.message_id).message, 'agent-id');
  assert.deepEqual(warnings, ['dingtalk_dws_record_send_failed', 'dingtalk_dws_gate_sent_failed']);
});

test('DWS reports an uncertain send without resubmitting it', async () => {
  let calls = 0;
  await assert.rejects(sendDws({ run: async () => {
    calls++;
    return { code: 0, stdout: '{"deliveryStatus":"unknown","openMessageId":"possible-send"}' };
  } }, config, { group, key: 'core-key', text: 'hello' }), /did not confirm/);
  assert.equal(calls, 1);
});

test('DWS stop reaps a consumer that ignores graceful shutdown', async () => {
  const registry = new ProcessRegistry(undefined, { detached: false });
  const dws = new DwsSubscription(registry, config, {}, () => {}, () => {});
  const child = registry.register(spawn(process.execPath, ['-e',
    'process.on("SIGTERM", () => {}); process.stdin.resume(); setInterval(() => {}, 1000); console.log("ready")',
  ], { detached: registry.detached, stdio: ['pipe', 'pipe', 'pipe'] }));
  dws.children.set(child, {});
  await once(child.stdout, 'data');
  const closed = once(child, 'close');
  await dws.stop();
  await closed;
  assert.equal(registry.children.has(child), false);
});

test('DWS reports unconfirmed teardown when a descendant holds consumer output open', {
  skip: process.platform === 'win32' && 'the fixture reproduces inherited-pipe teardown on POSIX',
}, async (t) => {
  const registry = new ProcessRegistry(undefined, { detached: false });
  const failure = Promise.withResolvers();
  const dws = new DwsSubscription(registry, config, { warn() {} }, () => {},
    (exit, teardownFailed) => failure.resolve({ exit, teardownFailed }));
  const script = `
    const child = require('node:child_process').spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'],
      { stdio: ['ignore', 'inherit', 'inherit'] });
    console.log(child.pid);
    child.unref();
  `;
  const consumer = registry.register(spawn(process.execPath, ['-e', script],
    { detached: registry.detached, stdio: ['pipe', 'pipe', 'pipe'] }));
  const exited = once(consumer, 'exit');
  const closed = t.mock.fn();
  consumer.once('close', closed);
  const [chunk] = await once(consumer.stdout, 'data');
  const descendant = Number(chunk.toString().trim());
  t.after(() => {
    try { process.kill(descendant, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
  });
  await exited;
  assert.equal(closed.mock.callCount(), 0);
  dws.children.set(consumer, {});
  dws.fail('fixture consumer failed');
  const { exit, teardownFailed } = await failure.promise;
  assert.equal(teardownFailed, true);
  assert.match(exit.stderr, /did not close before the stop deadline/);
  await assert.rejects(dws.stop(), /did not close before the stop deadline/);
});

test('DWS reports a post-stage pruning failure and resumes the unchanged journal after restart', async (t) => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'ovsa-dingtalk-recover-stage-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const state = new DingTalkState(directory, config.profile);
  const failed = t.mock.fn();
  const released = t.mock.fn();
  const gate = new DingTalkGate(state, released, failed);
  const prune = t.mock.method(state, 'pruneContext', () => { throw new Error('fixture prune failed'); });
  gate.stage({ id: 'saved' }, group, 'direct');
  gate.stage({ id: 'saved' }, group, 'direct');
  assert.equal(state.pendingGroup(group).length, 1);
  assert.equal(released.mock.callCount(), 0);
  assert.equal(failed.mock.callCount(), 1);
  prune.mock.restore();
  gate.stop();
  state.close();

  const restored = new DingTalkState(directory, config.profile);
  const restartedGate = new DingTalkGate(restored, (key) => {
    for (const { seq } of restored.pendingGroup(key)) restored.delivered(seq);
    released(key);
  }, (error) => assert.fail(error));
  try {
    restartedGate.resume();
    assert.deepEqual(released.mock.calls.map(({ arguments: args }) => args), [[group]]);
    assert.deepEqual(restored.pendingGroup(group), []);
  } finally {
    restartedGate.stop();
    restored.close();
  }
});

test('DWS pruning removes expired context from inactive groups and preserves pending work', async (t) => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'ovsa-dingtalk-prune-'));
  const state = new DingTalkState(directory, config.profile);
  t.after(async () => { state.close(); await rm(directory, { recursive: true, force: true }); });
  const inactive = 'inactive-group';
  const now = t.mock.timers;
  now.enable({ apis: ['Date'], now: 10 });
  state.stage({ id: 'expired-context' }, inactive, 'context');
  state.stage({ id: 'pending-work' }, inactive, 'ambient');
  now.tick(100);
  state.stage({ id: 'recent-context' }, inactive, 'context');
  state.pruneContext(group, 50, 30);
  assert.deepEqual(state.pendingGroup(inactive).map(({ envelope }) => JSON.parse(envelope).id),
    ['pending-work', 'recent-context']);
});
