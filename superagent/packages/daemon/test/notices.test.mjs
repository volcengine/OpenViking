// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
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

  it('notifies a conversation once and mutes every later failure there', async () => {
    const { policy, replies, activities } = makePolicy({ 1: inbound(1), 2: inbound(2), 3: inbound(3) });
    await policy.notifyTurnFailure({ inboundSeqs: [1], error: 'model exploded' });
    await policy.notifyTurnFailure({ inboundSeqs: [2], error: 'model exploded' });
    await policy.notifyTurnFailure({ inboundSeqs: [3], error: 'quota exceeded' });
    assert.equal(replies.length, 1);
    assert.match(replies[0].text, /处理失败/);
    const notices = activities.filter((entry) => entry.kind === 'agent.failure_notice');
    assert.deepEqual(notices.map((entry) => entry.status), ['NOTIFYING', 'MUTED', 'MUTED']);
  });

  it('stays muted however long the failure lasts', async () => {
    const { policy, replies } = makePolicy({ 1: inbound(1), 2: inbound(2) });
    await policy.notifyTurnFailure({ inboundSeqs: [1], error: 'model exploded' });
    const key = inbound(1).chatId;
    const mute = policy.failureNoticeMutes.get(key);
    policy.failureNoticeMutes.set(key, { ...mute, atMs: mute.atMs - 24 * 60 * 60 * 1_000 });
    await policy.notifyTurnFailure({ inboundSeqs: [2], error: 'model exploded' });
    assert.equal(replies.length, 1);
  });

  it('always notifies when a later failed message mentions the bot', async () => {
    const rows = {
      1: inbound(1),
      2: inbound(2, { mentions: [{ key: '@_user_1', name: 'Alpha', id: 'ou_bot123456' }] }),
    };
    const mentionTarget = (row) => mentionTargetOf(row.mentions, 'ou_bot123456');
    const { policy, replies } = makePolicy(rows, { mentionTarget });
    await policy.notifyTurnFailure({ inboundSeqs: [1], error: 'model exploded' });
    await policy.notifyTurnFailure({ inboundSeqs: [2], error: 'model exploded' });
    assert.equal(replies.length, 2);
  });

  it('treats threads of the same group as one chat', async () => {
    const threaded = inbound(2, { threadId: 'omt_thread1234', conversationKey: 'profile:tenant:oc_chat123456:omt_thread1234' });
    const { policy, replies } = makePolicy({ 1: inbound(1), 2: threaded });
    await policy.notifyTurnFailure({ inboundSeqs: [1], error: 'model exploded' });
    await policy.notifyTurnFailure({ inboundSeqs: [2], error: 'model exploded' });
    assert.equal(replies.length, 1);
  });

  it('still notifies a different conversation once', async () => {
    const other = inbound(2, { chatId: 'oc_other', conversationKey: 'profile:tenant:oc_other:main' });
    const { policy, replies } = makePolicy({ 1: inbound(1), 2: other });
    await policy.notifyTurnFailure({ inboundSeqs: [1], error: 'model exploded' });
    await policy.notifyTurnFailure({ inboundSeqs: [2], error: 'model exploded' });
    assert.equal(replies.length, 2);
  });

  it('clears every mute when any turn succeeds', async () => {
    const other = inbound(2, { chatId: 'oc_other', conversationKey: 'profile:tenant:oc_other:main' });
    const { policy, replies } = makePolicy({ 1: inbound(1), 2: other, 3: inbound(3) });
    await policy.notifyTurnFailure({ inboundSeqs: [1], error: 'model exploded' });
    // Recovery seen in another conversation: the runtime works again.
    policy.onTurnSucceeded({ inboundSeqs: [2] });
    assert.equal(policy.failureNoticeMutes.size, 0);
    await policy.notifyTurnFailure({ inboundSeqs: [3], error: 'model exploded' });
    assert.equal(replies.length, 2);
  });
});
