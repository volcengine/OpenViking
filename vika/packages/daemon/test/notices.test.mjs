// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { Store } from '../src/store.mjs';
import { describe, it } from 'node:test';
import { createLogger } from '../src/logger.mjs';
import { FailurePolicy } from '../src/notices.mjs';
import { mentionTarget as mentionTargetOf } from '../src/runtime-input.mjs';
import { chatEvent } from './event-fixture.mjs';

describe('automatic claude fallback', () => {
  function makeFallbackPolicy({ runtime = 'claude', setModelError = null, fallbackModel } = {}) {
    const calls = [];
    const delivered = [];
    const replies = [];
    const activities = [];
    const agentRuntime = {
      runtime,
      async transition(request) {
        assert.equal(request.kind, 'select-runtime');
        assert.equal(request.runtime, 'codex');
        calls.push(['setModel', request.model]);
        if (setModelError) throw setModelError;
        this.runtime = 'codex';
        return { ok: true, runtimeChanged: true, runtime: 'codex', model: request.model };
      },
      submit(items) { delivered.push(items); return { accepted: true }; },
    };
    const inboundRows = {
      1: {
        seq: 1,
        messageId: 'om_failed_123456',
        chatId: 'oc_chat123456',
        threadId: null,
        conversationKey: 'profile:tenant:oc_chat123456:main',
        senderId: 'ou_sender123456',
        content: '帮我看下这个',
        createTimeMs: Date.UTC(2026, 7, 7, 10, 0),
        mentions: [],
      },
    };
    const policy = new FailurePolicy({
      config: fallbackModel === undefined ? { fallbackModel: 'gpt-5.6-sol' } : { fallbackModel },
      store: {
        getCognitionState: () => ({ runtime: agentRuntime.runtime }),
        inboundBySeq: (seq) => inboundRows[seq] ? chatEvent(inboundRows[seq]) : null,
      },
      actions: {
        async replyToEvent(event, text) { replies.push({ messageId: event.messageId, text }); return { ok: true }; },
      },
      logger: createLogger('error', { test: 'claude-fallback' }),
      runtime: agentRuntime,
      recordActivity(kind, fields) { activities.push({ kind, ...fields }); return true; },
    });
    return { policy, agentRuntime, calls, delivered, replies, activities };
  }

  it('hard-switches to the fallback model, notifies the chat, and retries the failed turn', async () => {
    const { policy, calls, delivered, replies, activities } = makeFallbackPolicy();
    assert.equal(
      await policy.onTurnFailed({ inboundSeqs: [1], error: 'Claude API exploded' }),
      true,
    );

    assert.deepEqual(calls, [['setModel', 'gpt-5.6-sol']]);
    assert.equal(replies.length, 1);
    assert.equal(replies[0].messageId, 'om_failed_123456');
    assert.match(replies[0].text, /因 Claude API exploded 报错，已自动切换到 gpt-5\.6-sol 重试/);
    assert.doesNotMatch(replies[0].text, /没有生成回复/); // no duplicate failure notice
    assert.equal(delivered.length, 1);
    assert.equal(delivered[0][0].seq, 1);
    assert.match(delivered[0][0].text, /^\[chat=oc_chat123456 source=lark msg=om_failed_123456 .*\] 帮我看下这个$/);
    const fallback = activities.find((entry) => entry.kind === 'agent.fallback');
    assert.equal(fallback.status, 'SWITCHED');
    assert.equal(fallback.metadata.model, 'gpt-5.6-sol');
  });

  it('honours FALLBACK_MODEL from config', async () => {
    const { policy, calls } = makeFallbackPolicy({ fallbackModel: 'gpt-6-luna' });
    await policy.onTurnFailed({ inboundSeqs: [1], error: 'boom' });
    assert.deepEqual(calls, [['setModel', 'gpt-6-luna']]);
  });

  it('never falls back from the codex runtime and sends the normal notice instead', async () => {
    const { policy, calls, replies } = makeFallbackPolicy({ runtime: 'codex' });
    assert.equal(
      await policy.onTurnFailed({ inboundSeqs: [1], error: 'quota exceeded' }),
      false,
    );
    assert.equal(calls.length, 0);
    assert.equal(replies.length, 1);
    assert.match(replies[0].text, /处理失败/);
  });

  it('attempts only one automatic fallback per cooldown window', async () => {
    const { policy, agentRuntime, calls, replies } = makeFallbackPolicy();
    await policy.onTurnFailed({ inboundSeqs: [1], error: 'boom' });
    assert.equal(calls.length, 1);
    // The user switched back to claude, which fails again inside the cooldown.
    agentRuntime.runtime = 'claude';
    await policy.onTurnFailed({ inboundSeqs: [1], error: 'boom again' });
    assert.equal(calls.length, 1); // no second automatic switch
    assert.equal(replies.length, 2);
    assert.match(replies[1].text, /处理失败/);
    // After the cooldown expires the fallback becomes available again.
    policy.lastFallbackAtMs = Date.now() - 6 * 60 * 1_000;
    await policy.onTurnFailed({ inboundSeqs: [1], error: 'boom again' });
    assert.equal(calls.length, 2);
  });

  it('falls through to the normal failure notice when the switch itself fails', async () => {
    const { policy, calls, delivered, replies, activities } = makeFallbackPolicy({
      setModelError: new Error('codex is unavailable'),
    });
    await policy.onTurnFailed({ inboundSeqs: [1], error: 'boom' });
    assert.equal(calls.length, 1);
    assert.equal(delivered.length, 0);
    assert.equal(replies.length, 1);
    assert.match(replies[0].text, /处理失败/);
    const fallback = activities.find((entry) => entry.kind === 'agent.fallback');
    assert.equal(fallback.status, 'FAILED');
  });
});

describe('failure notice mute', () => {
  function makePolicy(inboundRows, { mentionTarget } = {}) {
    const replies = [];
    const activities = [];
    const policy = new FailurePolicy({
      config: {},
      mentionTarget,
      store: {
        getCognitionState: () => ({ runtime: 'codex' }),
        inboundBySeq: (seq) => inboundRows[seq] ?? null,
      },
      actions: {
        async replyToEvent(event, text) { replies.push({ messageId: event.messageId, text }); return { ok: true }; },
      },
      logger: createLogger('error', { test: 'failure-mute' }),
      runtime: {},
      recordActivity(kind, fields) { activities.push({ kind, ...fields }); return true; },
    });
    return { policy, replies, activities };
  }

  const inbound = (seq, overrides = {}) => chatEvent({
    seq,
    messageId: `om_failed_${seq}`,
    chatId: 'oc_chat123456',
    threadId: null,
    conversationKey: 'profile:tenant:oc_chat123456:main',
    mentions: [],
    ...overrides,
  });

  it('mutes a repeat of the same error in the same conversation', async () => {
    const { policy, replies, activities } = makePolicy({ 1: inbound(1), 2: inbound(2) });
    await policy.notifyTurnFailure({ inboundSeqs: [1], error: 'model exploded' });
    await policy.notifyTurnFailure({ inboundSeqs: [2], error: 'model exploded' });
    assert.equal(replies.length, 1);
    assert.match(replies[0].text, /处理失败/);
    const notices = activities.filter((entry) => entry.kind === 'agent.failure_notice');
    assert.deepEqual(notices.map((entry) => entry.status), ['NOTIFYING', 'MUTED']);
  });

  it('notifies again when the error summary changes', async () => {
    const { policy, replies } = makePolicy({ 1: inbound(1), 2: inbound(2), 3: inbound(3) });
    await policy.notifyTurnFailure({ inboundSeqs: [1], error: 'model exploded' });
    await policy.notifyTurnFailure({ inboundSeqs: [2], error: 'quota exceeded' });
    assert.equal(replies.length, 2);
    assert.match(replies[1].text, /quota exceeded/);
    // The stored summary was updated: repeating the new error is now muted.
    await policy.notifyTurnFailure({ inboundSeqs: [3], error: 'quota exceeded' });
    assert.equal(replies.length, 2);
  });

  it('re-notifies a persistent identical failure after the mute TTL expires', async () => {
    const { policy, replies } = makePolicy({ 1: inbound(1), 2: inbound(2) });
    await policy.notifyTurnFailure({ inboundSeqs: [1], error: 'model exploded' });
    assert.equal(replies.length, 1);
    // Age the delivered mute past the 10-minute TTL.
    const key = inbound(1).conversationKey;
    const mute = policy.failureNoticeMutes.get(key);
    policy.failureNoticeMutes.set(key, { ...mute, atMs: mute.atMs - 11 * 60 * 1_000 });
    await policy.notifyTurnFailure({ inboundSeqs: [2], error: 'model exploded' });
    assert.equal(replies.length, 2);
  });

  it('resets the mute after a successful turn for that conversation', async () => {
    const { policy, replies } = makePolicy({ 1: inbound(1), 2: inbound(2), 3: inbound(3) });
    await policy.notifyTurnFailure({ inboundSeqs: [1], error: 'model exploded' });
    policy.onTurnSucceeded({ inboundSeqs: [2] });
    await policy.notifyTurnFailure({ inboundSeqs: [3], error: 'model exploded' });
    assert.equal(replies.length, 2);
  });

  it('keeps the mute when the successful turn belongs to another conversation', async () => {
    const other = inbound(2, { chatId: 'oc_other' });
    const { policy, replies } = makePolicy({ 1: inbound(1), 2: other, 3: inbound(3) });
    await policy.notifyTurnFailure({ inboundSeqs: [1], error: 'model exploded' });
    policy.onTurnSucceeded({ inboundSeqs: [2] });
    await policy.notifyTurnFailure({ inboundSeqs: [3], error: 'model exploded' });
    assert.equal(replies.length, 1);
  });

  it('always notifies when an inbound message of the failed turn mentions the bot', async () => {
    const rows = {
      1: inbound(1),
      2: inbound(2, { mentions: [{ key: '@_user_1', name: 'Alpha', id: 'ou_bot123456' }] }),
    };
    const { policy, replies, activities } = makePolicy(rows);
    await policy.notifyTurnFailure({ inboundSeqs: [1], error: 'model exploded' });
    await policy.notifyTurnFailure({ inboundSeqs: [2], error: 'model exploded' });
    assert.equal(replies.length, 2);
    const notices = activities.filter((entry) => entry.kind === 'agent.failure_notice');
    assert.deepEqual(notices.map((entry) => entry.status), ['NOTIFYING', 'NOTIFYING']);
  });

  it('keeps the mute for a message that only mentions someone else, once the bot knows its id', async () => {
    const rows = {
      1: inbound(1),
      2: inbound(2, { mentions: [{ key: '@_user_1', name: 'Alpha', id: 'ou_other123456' }] }),
      3: inbound(3, { mentions: [{ key: '@_user_1', name: 'Alpha', id: 'ou_bot123456' }] }),
    };
    const mentionTarget = (row) => mentionTargetOf(row.mentions, 'ou_bot123456');
    const { policy, activities } = makePolicy(rows, { mentionTarget });
    for (const seq of [1, 2, 3]) await policy.notifyTurnFailure({ inboundSeqs: [seq], error: 'model exploded' });
    const notices = activities.filter((entry) => entry.kind === 'agent.failure_notice');
    assert.deepEqual(notices.map((entry) => entry.status), ['NOTIFYING', 'MUTED', 'NOTIFYING']);
  });
});

describe('failures after durable decisions', () => {
  function fixture(outcomes) {
    const replies = [];
    const transitions = [];
    const rows = [1, 2].map((seq) => chatEvent({ seq, chatId: `chat-${seq}`, messageId: `message-${seq}` }));
    const policy = new FailurePolicy({
      config: {},
      store: {
        inboundBySeq: (seq) => rows.find((row) => row.seq === seq),
        coveringDecision: (_scope, seq) => outcomes[seq] ?? null,
        successfulDecisionDelivery: (_scope, seq) => outcomes[seq] === 'delivered',
        getCognitionState: () => ({ runtime: 'claude' }),
      },
      actions: { replyToEvent: async (row) => { replies.push(row.seq); return { ok: true }; } },
      runtime: { transition: async (request) => { transitions.push(request); return { ok: true }; }, submit() {} },
      logger: createLogger('error'), recordActivity() {},
    });
    return { policy, replies, transitions };
  }

  for (const outcome of ['delivered', 'silent']) {
    it(`does not retry or notify an input already ${outcome}`, async () => {
      const { policy, replies, transitions } = fixture({ 1: outcome });
      await policy.onTurnFailed({ inboundSeqs: [1], error: 'pi stop: error' });
      await policy.notifyTurnFailure({ inboundSeqs: [1], error: 'pi stop: error' });
      assert.deepEqual(replies, []);
      assert.deepEqual(transitions, []);
    });
  }

  it('notifies the unanswered scope even when the last input was delivered', async () => {
    const { policy, replies } = fixture({ 2: 'delivered' });
    await policy.notifyTurnFailure({ inboundSeqs: [1, 2], error: 'pi stop: error' });
    assert.deepEqual(replies, [1]);
  });

  it('notifies uncertain delivery separately before retrying an unanswered scope', async () => {
    const { policy, replies, transitions } = fixture({ 2: 'delivered' });
    policy.store.successfulDecisionDelivery = () => false;
    let submitted;
    policy.runtime.submit = (items) => { submitted = items; };
    await policy.onTurnFailed({ inboundSeqs: [1, 2], error: 'upstream error' });
    assert.deepEqual(replies, [2, 1]);
    assert.equal(transitions.length, 1);
    assert.deepEqual(submitted.map((item) => item.seq), [1]);
  });

  it('still retries unanswered inputs without redelivering completed ones', async () => {
    const { policy, replies, transitions } = fixture({ 2: 'delivered' });
    let submitted;
    policy.runtime.submit = (items) => { submitted = items; };
    await policy.onTurnFailed({ inboundSeqs: [1, 2], error: 'upstream error' });
    assert.equal(transitions.length, 1);
    assert.deepEqual(replies, [1]);
    assert.deepEqual(submitted.map((item) => item.seq), [1]);
  });
});


describe('failure notices use confirmed outbox delivery', () => {
  for (const status of ['PENDING', 'UNCERTAIN', 'SENT']) {
    it(`handles a reserved decision with ${status} outbox status`, async (t) => {
      const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-failure-outbox-'));
      const store = new Store(path.join(dir, 'test.sqlite3'), createLogger('error'));
      t.after(() => { store.close(); fs.rmSync(dir, { recursive: true, force: true }); });
      const older = store.ingest(chatEvent({ messageId: 'older' })).inbound;
      const newer = store.ingest(chatEvent({ messageId: 'newer' })).inbound;
      const row = store.db.prepare('SELECT * FROM connector_events WHERE seq = ?').get(newer.seq);
      store.recordDecision({ row, outcome: 'delivered' });
      const delivery = store.reserveOutbox({ connector: 'lark', action: 'message.reply',
        idempotencyKey: 'final', payload: {}, caller: 'agent', inboundSeq: older.seq,
        metadata: { finalDecisionSeq: newer.seq } });
      if (status !== 'PENDING') store.settleOutbox(delivery.id, status);
      const replies = [];
      const policy = new FailurePolicy({ config: {}, store,
        actions: { replyToEvent: async (event) => { replies.push(event.seq); return { ok: true }; } },
        logger: createLogger('error'), recordActivity() {},
        runtime: { transition() { throw new Error('must not retry an attempted delivery'); } },
      });
      await policy.onTurnFailed({ inboundSeqs: [newer.seq], error: 'late error' });
      assert.deepEqual(replies, status === 'SENT' ? [] : [newer.seq]);
      assert.equal(store.successfulDecisionDelivery(newer.scopeKey, newer.seq), status === 'SENT');
      assert.equal(store.successfulDecisionDelivery('another scope', older.seq), false);
    });
  }
});
