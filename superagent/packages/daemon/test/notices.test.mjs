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
    const ownerNotices = [];
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
        async notifyOwnerForEvent(event, text) { ownerNotices.push({ messageId: event.messageId, text }); return { ok: true }; },
      },
      logger: createLogger('error', { test: 'claude-fallback' }),
      runtime: agentRuntime,
      recordActivity(kind, fields) { activities.push({ kind, ...fields }); return true; },
    });
    return { policy, agentRuntime, calls, delivered, replies, ownerNotices, activities };
  }

  it('hard-switches to the fallback model, notifies only the owner, and retries the failed turn', async () => {
    const { policy, calls, delivered, replies, ownerNotices, activities } = makeFallbackPolicy();
    assert.equal(
      await policy.onTurnFailed({ inboundSeqs: [1], error: 'Claude API exploded' }),
      true,
    );

    assert.deepEqual(calls, [['setModel', 'gpt-5.6-sol']]);
    assert.equal(replies.length, 0);
    assert.equal(ownerNotices.length, 1);
    assert.equal(ownerNotices[0].messageId, 'om_failed_123456');
    assert.match(ownerNotices[0].text, /已自动切换到 gpt-5\.6-sol 重试/);
    assert.match(ownerNotices[0].text, /受影响消息/);
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

  it('never falls back from the codex runtime and sends recoverable faults only to the owner', async () => {
    const { policy, calls, replies, ownerNotices } = makeFallbackPolicy({ runtime: 'codex' });
    assert.equal(
      await policy.onTurnFailed({ inboundSeqs: [1], error: 'quota exceeded' }),
      false,
    );
    assert.equal(calls.length, 0);
    assert.equal(replies.length, 0);
    assert.equal(ownerNotices.length, 1);
    assert.match(ownerNotices[0].text, /quota exceeded/);
  });

  it('attempts only one automatic fallback per cooldown window', async () => {
    const { policy, agentRuntime, calls, replies, ownerNotices } = makeFallbackPolicy();
    await policy.onTurnFailed({ inboundSeqs: [1], error: 'boom' });
    assert.equal(calls.length, 1);
    // The user switched back to claude, which fails again inside the cooldown.
    agentRuntime.runtime = 'claude';
    await policy.onTurnFailed({ inboundSeqs: [1], error: 'boom again' });
    assert.equal(calls.length, 1); // no second automatic switch
    assert.equal(replies.length, 1);
    assert.match(replies[0].text, /处理异常/);
    // After the cooldown expires the fallback becomes available again.
    policy.lastFallbackAtMs = Date.now() - 6 * 60 * 1_000;
    await policy.onTurnFailed({ inboundSeqs: [1], error: 'boom again' });
    assert.equal(calls.length, 2);
    assert.equal(ownerNotices.length, 2);
  });

  it('falls through to the normal failure notice when the switch itself fails', async () => {
    const { policy, calls, delivered, replies, activities } = makeFallbackPolicy({
      setModelError: new Error('codex is unavailable'),
    });
    await policy.onTurnFailed({ inboundSeqs: [1], error: 'boom' });
    assert.equal(calls.length, 1);
    assert.equal(delivered.length, 0);
    assert.equal(replies.length, 1);
    assert.match(replies[0].text, /处理异常/);
    const fallback = activities.find((entry) => entry.kind === 'agent.fallback');
    assert.equal(fallback.status, 'FAILED');
  });

  it('keeps a recoverable fault private when the runtime switch fails', async () => {
    const { policy, replies, ownerNotices } = makeFallbackPolicy({
      setModelError: new Error('codex is unavailable'),
    });
    assert.equal(await policy.onTurnFailed({ inboundSeqs: [1], error: "You've hit your 5-hour limit" }), false);
    assert.equal(replies.length, 0);
    assert.equal(ownerNotices.length, 1);
    assert.match(ownerNotices[0].text, /5-hour limit/);
  });
});

describe('configurable runtime fallback', () => {
  function fixture(t, overrides = {}) {
    let now = 1_000_000;
    let timer = null;
    let finish;
    const calls = [];
    const delivered = [];
    const replies = [];
    const ownerNotices = [];
    const runtime = {
      runtime: 'traex', model: 'main-model', revision: 1, held: false,
      snapshot() { return { revision: this.revision }; },
      holdAdmission() { this.held = true; return () => { this.held = false; }; },
      async transition(request) {
        calls.push(request);
        if (request.whenIdle) await new Promise(resolve => { finish = resolve; });
        if (request.whenIdle && overrides.returnFails) throw new Error('primary unavailable');
        this.runtime = request.runtime;
        this.revision += 1;
        return { ok: true };
      },
      submit(items) { delivered.push(items); return { accepted: true }; },
    };
    const row = chatEvent({ seq: 1, messageId: 'message', chatId: 'chat',
      conversationKey: 'conversation', senderId: 'sender', content: 'task', mentions: [] });
    const policy = new FailurePolicy({
      config: { initialModel: 'traex', fallbackRuntime: 'pi', ...overrides },
      store: { getCognitionState: () => ({ runtime: runtime.runtime }), inboundBySeq: () => row },
      actions: {
        async replyToEvent(event, text) { replies.push(text); return { ok: true }; },
        async notifyOwnerForEvent(event, text) {
          ownerNotices.push(text);
          if (overrides.noticeFails) throw new Error('notice delivery failed');
          return { ok: true };
        },
      },
      logger: createLogger('error'), runtime, recordActivity() {},
      clock: () => now,
      setTimer: (callback, ms) => { timer = { callback, ms }; return timer; },
      clearTimer: () => { timer = null; },
    });
    t.after(() => policy.stop());
    return { policy, runtime, calls, delivered, replies, ownerNotices,
      timer: () => timer, advance: ms => { now += ms; }, finish: () => finish() };
  }

  it('keeps saved backup model settings and returns after a fixed ten-minute window', async t => {
    const f = fixture(t);
    assert.equal(await f.policy.onTurnFailed({ inboundSeqs: [1], error: 'empty stream (3083)' }), true);
    assert.equal(f.calls[0].runtime, 'pi');
    assert.equal(f.calls[0].model, undefined);
    assert.equal(f.calls[0].persistPreference, false);
    assert.equal(f.timer().ms, 600_000);
    f.advance(100_000);
    f.policy.onTurnSucceeded();
    assert.equal(f.timer().ms, 600_000);
    const returning = f.policy.returnToPrimary(f.policy.backupWindow);
    assert.equal(f.runtime.held, true);
    assert.equal(f.runtime.runtime, 'pi');
    assert.equal(f.calls[1].whenIdle, true);
    assert.equal(f.calls[1].model, 'main-model');
    f.finish();
    await returning;
    assert.equal(f.runtime.runtime, 'traex');
    assert.equal(f.runtime.held, false);
  });

  it('recognizes provider usage-window errors without exposing them to the chat', async t => {
    const f = fixture(t);
    assert.equal(await f.policy.onTurnFailed({ inboundSeqs: [1],
      error: "You've hit your 5-hour limit · resets 10pm (Asia/Shanghai)" }), true);
    assert.equal(f.calls.length, 1);
    assert.equal(f.replies.length, 0);
    assert.equal(f.ownerNotices.length, 1);
  });

  it('keeps admission held if the primary cannot restart', async t => {
    const f = fixture(t, { returnFails: true });
    await f.policy.onTurnFailed({ inboundSeqs: [1], error: 'timeout' });
    const returning = f.policy.returnToPrimary(f.policy.backupWindow);
    f.finish(); await returning;
    assert.equal(f.runtime.held, true);
    assert.equal(f.replies.length, 0);
    assert.match(f.ownerNotices.at(-1), /恢复失败/);
    f.policy.onRuntimeSelected('pi');
    assert.equal(f.runtime.held, false);
    assert.equal(f.policy.backupWindow, null);
  });

  it('does not enqueue a second return while accepted work is draining', async t => {
    const f = fixture(t);
    await f.policy.onTurnFailed({ inboundSeqs: [1], error: 'timeout' });
    const window = f.policy.backupWindow;
    const returning = f.policy.returnToPrimary(window);
    await f.policy.returnToPrimary(window);
    assert.equal(f.calls.length, 2);
    f.finish(); await returning;
    assert.equal(f.runtime.held, false);
  });

  it('rejects non-retryable HTTP errors even when their message mentions an upstream timeout', async t => {
    const f = fixture(t);
    for (const status of [400, 401, 403, 404, 422]) {
      assert.equal(await f.policy.onTurnFailed({ inboundSeqs: [1],
        error: Object.assign(new Error('upstream request timeout'), { status }) }), false);
    }
    assert.equal(f.calls.length, 0);
  });

  it('does not override a manual runtime or model change', async t => {
    const f = fixture(t);
    await f.policy.onTurnFailed({ inboundSeqs: [1], error: 'timeout' });
    f.runtime.revision += 1;
    await f.policy.returnToPrimary(f.policy.backupWindow);
    assert.equal(f.calls.length, 1);
    assert.equal(f.policy.backupWindow, null);
  });

  it('allows a zero return interval for legacy sticky fallback', async t => {
    const f = fixture(t, { fallbackReturnAfterMs: 0 });
    await f.policy.onTurnFailed({ inboundSeqs: [1], error: 'timeout' });
    assert.equal(f.timer(), null);
  });

  it('does not recursively fallback or retry tool errors, denied credentials, cancellation or business failures', async t => {
    const f = fixture(t);
    for (const error of ['tool failed', 'permission denied', 'cancelled', 'business error', 'invalid parameters']) {
      assert.equal(await f.policy.onTurnFailed({ inboundSeqs: [1], error }), false);
    }
    assert.equal(f.calls.length, 0);
    await f.policy.onTurnFailed({ inboundSeqs: [1], error: 'timeout' });
    assert.equal(await f.policy.onTurnFailed({ inboundSeqs: [1], error: 'timeout' }), false);
    assert.equal(f.calls.length, 1);
  });

  it('retries the failed input even when fallback notice delivery fails', async t => {
    const f = fixture(t, { noticeFails: true, fallbackReturnAfterMs: 250 });
    assert.equal(await f.policy.onTurnFailed({ inboundSeqs: [1], error: 'timeout' }), true);
    assert.equal(f.delivered.length, 1);
    assert.equal(f.timer().ms, 250);
  });

  it('keeps a recoverable backup failure out of the user conversation', async t => {
    const f = fixture(t);
    f.runtime.runtime = 'pi';
    assert.equal(await f.policy.onTurnFailed({ inboundSeqs: [1], error: 'quota exceeded' }), false);
    assert.equal(f.replies.length, 0);
    assert.equal(f.ownerNotices.length, 1);
    assert.match(f.ownerNotices[0], /quota exceeded/);
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
    assert.match(replies[0].text, /处理异常/);
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
