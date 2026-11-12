// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// DSH adapter suite — activity mapping, queue bounds and crash recovery.
// Every case drives a real DshAgent over a fake bridge process; the fake child
// and the store double live in ./dsh-agent-fixture.mjs.

import assert from 'node:assert/strict';
import { DshAgent } from '../src/dsh.mjs';
import { after } from 'node:test';
import { describe, it } from './dsh-node-gate.mjs';
import {
  FakeChild, baseConfig, flush, makeStore, newAgent, startAgent, waitFor, acknowledge, event, settle,
} from './dsh-agent-fixture.mjs';

describe('DshAgent activities and unattended requests', () => {
  it('accumulates usage, maps tools and notes, declines approvals, and drops oversized lines', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    agent.deliver('do work', 7);
    await acknowledge(child, child.requests('session/steer')[0]);
    const root = agent.threadId;
    event(child, root, 'assistant/message', {
      message: { content: [{ type: 'text', text: 'note' }, { type: 'reasoning', text: 'thinking' }] },
      usage: { inputTokens: 10, cacheReadTokens: 4, outputTokens: 5, reasoningTokens: 2 },
    });
    event(child, root, 'assistant/message', {
      message: { content: [] },
      usage: { inputTokens: 3, cacheReadTokens: 1, outputTokens: 2, reasoningTokens: 1 },
    });
    event(child, root, 'tool/call', { callId: 'call-1', name: 'mcp__personal_agent__send_message', arguments: '{"text":"hello"}' });
    event(child, root, 'tool/result', { message: { toolCallId: 'call-1', content: [{ type: 'text', text: 'sent' }], isError: false } });
    child.feed({ jsonrpc: '2.0', id: 'server-approval', method: 'approval/request', params: { sessionId: root, toolName: 'bash' } });
    assert.deepEqual(child.sent().find((frame) => frame.id === 'server-approval').result, { outcome: 'rejected' });
    assert.ok(context.store.activities.some((item) => item.kind === 'agent.interaction_declined'));
    child.stdout.emit('data', `${'x'.repeat(16_000_001)}\n`);
    assert.equal(child.exitCode, null);
    await settle(child, agent);
    const called = context.store.activities.find((item) => item.kind === 'agent.tool_called');
    assert.equal(called.metadata.toolServer, 'personal_agent');
    assert.equal(called.metadata.toolName, 'send_message');
    assert.equal(called.summary, 'personal_agent · send_message');
    assert.match(called.metadata.input, /hello/);
    assert.equal(context.store.activities.find((item) => item.kind === 'agent.tool_completed').status, 'COMPLETED');
    const completed = context.store.activities.find((item) => item.kind === 'agent.turn_completed');
    assert.equal(completed.metadata.inputTokens, 18);
    assert.equal(completed.metadata.cachedInputTokens, 5);
    assert.equal(completed.metadata.outputTokens, 7);
    assert.equal(completed.metadata.reasoningTokens, 3);
    assert.equal(completed.metadata.costUsd, undefined);
    assert.ok(context.store.activities.some((item) => item.kind === 'agent.note'));
    assert.ok(context.store.activities.some((item) => item.kind === 'agent.thinking'));
    await agent.stop();
  });
});

describe('DshAgent activity keys and bounds', () => {
  it('records two runs on the same session under distinct activity keys', async () => {
    const store = makeStore();
    // Model the store's UNIQUE event_key + INSERT OR IGNORE: a repeat is dropped.
    const accepted = [];
    const seen = new Set();
    store.recordActivity = (activity) => {
      if (seen.has(activity.eventKey)) return false;
      seen.add(activity.eventKey);
      accepted.push(activity.eventKey);
      return true;
    };
    const config = baseConfig();
    const first = newAgent({ store, config });
    await startAgent(first);
    const sessionId = first.agent.threadId;
    await first.agent.stop();
    // A second daemon generation resumes the same session and records the same
    // logical events (agent.ready, turn_started) again.
    const second = newAgent({ store, config });
    await startAgent(second);
    await flush();
    assert.equal(second.agent.threadId, sessionId);
    await second.agent.stop();
    const ready = accepted.filter((key) => key.includes(':agent.ready:'));
    // Both runs' agent.ready rows survived the UNIQUE activity key.
    assert.equal(ready.length, 2, JSON.stringify(accepted));
    assert.notEqual(ready[0], ready[1]);
    assert.equal(new Set(accepted).size, accepted.length, JSON.stringify(accepted));
  });

  it('bounds the input queue and fails the overflow with a callback', async () => {
    const context = newAgent({ options: { maxQueuedItems: 2, maxQueueBatches: 2 } });
    const { agent } = context;
    await startAgent(context);
    // Disconnected input is queued; the cap fails the batch that would overflow.
    agent.connected = false;
    assert.equal(agent.deliver('one', 7, 'r7'), true);
    assert.equal(agent.deliver('two', 8, 'r8'), true);
    assert.equal(agent.deliver('three', 9, 'r9'), true);
    assert.equal(agent.queue.flat().length, 2);
    assert.equal(context.failures.length, 1);
    assert.deepEqual(context.failures[0].inboundSeqs, [9]);
    assert.match(context.failures[0].error, /queue is full/);
    assert.ok(context.warnings.some((entry) => entry.event === 'dsh_input_queue_full'));
    agent.connected = true;
    await agent.stop();
  });

  it('waits for drain when stdin reports backpressure and resumes on drain', async () => {
    const context = newAgent({ childOptions: { writeBlocked: true, silentMethods: ['session/steer'] } });
    const { agent } = context;
    const child = await startAgent(context);
    // The handshake's own write was refused, so the stream waits for `drain`.
    assert.ok(context.warnings.some((entry) => entry.event === 'dsh_stdin_backpressure'));
    assert.equal(agent.writeBlocked?.child, child);
    // Input that arrives while the stream is blocked waits in the bounded queue:
    // writing past a stream that asked to wait is what made the caps meaningless.
    agent.deliver('blocked write', 7);
    await flush();
    assert.equal(child.requests('session/steer').length, 0);
    assert.deepEqual(agent.queue.flat().map((item) => item.seq), [7]);
    child.drain();
    await waitFor(() => child.requests('session/steer').length === 1);
    assert.equal(agent.writeBlocked, null);
    assert.match(child.requests('session/steer')[0].params.contentBlocks[0].text, /blocked write/);
    await agent.stop();
  });

  it('re-issues an idle session/wait instead of failing a long turn', async () => {
    const context = newAgent({ options: { waitIdleTimeoutMs: 25 } });
    const { agent } = context;
    const child = await startAgent(context);
    agent.deliver('long turn', 7);
    await acknowledge(child, child.requests('session/steer')[0]);
    // The wait never answers: the idle bound re-issues it and warns.
    await waitFor(() => child.requests('session/wait').length >= 2, 2_000);
    assert.ok(context.warnings.some((entry) => entry.event === 'dsh_wait_idle_timeout'));
    // A long turn is legal: nothing has failed while it is still idle-waiting.
    assert.equal(context.failures.length, 0);
    assert.equal(context.successes.length, 0);
    assert.equal(agent.turn !== null, true);
    await settle(child, agent);
    assert.deepEqual(context.successes, [{ inboundSeqs: [7] }]);
    await agent.stop();
  });

  it('stops reconnecting after the configured attempt cap and fails the queue', async () => {
    const context = newAgent({ options: { reconnectMaxAttempts: 2 } });
    const { agent } = context;
    const first = await startAgent(context);
    agent._spawn = (bin, args, options) => {
      const child = new FakeChild({ hello: new Error('reconnect unavailable') });
      context.children.push(child);
      context.spawnCalls.push({ bin, args, options, child });
      return child;
    };
    first.finish(1, null);
    agent.deliver('cannot reconnect', 7);
    await waitFor(() => context.failures.length === 1, 3_000);
    assert.equal(agent.autoReconnect, false);
    assert.ok(context.warnings.some((entry) => entry.event === 'dsh_reconnect_exhausted'));
    assert.equal(context.children.length, 3);
    await agent.stop();
  });
});

describe('DshAgent crash recovery contract', () => {
  it('fails acknowledged input on a crash and never replays it', async () => {
    const context = newAgent();
    const { agent } = context;
    const first = await startAgent(context);
    agent.deliver('accepted work', 7, 'accepted-receipt');
    await acknowledge(first, first.requests('session/steer')[0], 'accepted-message');
    assert.equal(agent.turn.items.length, 1);
    first.finish(1, null);
    assert.equal(context.failures.length, 1);
    assert.deepEqual(context.failures[0].inboundSeqs, [7]);
    assert.equal(context.failures[0].error, 'dsh connection lost');
    assert.equal(agent.queue.flat().length, 0);
    const completed = context.store.activities.filter((item) => item.kind === 'agent.turn_completed').at(-1);
    assert.equal(completed.metadata.endReason, 'connection-lost');
    await waitFor(() => context.children.length === 2 && agent.connected);
    const second = context.children[1];
    // Nothing was unacknowledged, so the session is still resumable.
    assert.equal(agent.forceFreshSession, false);
    assert.equal(second.requests('session/resume').length, 1);
    await flush();
    // The acknowledged input is never written again.
    assert.equal(second.requests('session/steer').length, 0);
    assert.equal(context.successes.length, 0);
    await agent.stop();
  });

  it('reports a crashed turn once per conversation it held', async () => {
    // The daemon addresses its failure notice from the last inbound of the
    // callback, so one callback for a two-chat turn told only one of them.
    const store = makeStore({ chats: { 7: 'chat-A', 8: 'chat-B' } });
    const context = newAgent({ store });
    const { agent } = context;
    const first = await startAgent(context);
    agent.deliver('from chat A', 7, 'r-a');
    await acknowledge(first, first.requests('session/steer')[0], 'msg-a');
    agent.deliver('from chat B', 8, 'r-b');
    await acknowledge(first, first.requests('session/steer')[1], 'msg-b');
    first.finish(1, null);
    assert.deepEqual(
      context.failures.map((info) => [info.inboundSeqs, info.receiptIds]),
      [[[7], ['r-a']], [[8], ['r-b']]],
    );
    await waitFor(() => context.children.length === 2 && agent.connected);
    await flush();
    // Neither input is replayed: both were acknowledged by the crashed process.
    assert.equal(context.children[1].requests('session/steer').length, 0);
    await agent.stop();
  });

  it('replays only the unacknowledged part of a crashed turn', async () => {
    const context = newAgent();
    const { agent } = context;
    const first = await startAgent(context);
    agent.deliver('accepted work', 7, 'accepted-receipt');
    await acknowledge(first, first.requests('session/steer')[0], 'accepted-message');
    agent.deliver('written only', 8, 'ambiguous-receipt');
    first.finish(1, null);
    assert.deepEqual(context.failures.map((info) => info.inboundSeqs), [[7]]);
    assert.equal(agent.forceFreshSession, true);
    await waitFor(() => context.children.length === 2 && agent.connected);
    const second = context.children[1];
    await waitFor(() => second.requests('session/steer').length === 1);
    const replay = second.requests('session/steer')[0];
    assert.match(replay.params.contentBlocks[0].text, /written only/);
    assert.doesNotMatch(replay.params.contentBlocks[0].text, /accepted work/);
    await agent.stop();
  });
});
