// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// DSH adapter suite — turn state machine, input settlement and frame bounds.
// Every case drives a real DshAgent over a fake bridge process; the fake child
// and the store double live in ./dsh-agent-fixture.mjs.

import assert from 'node:assert/strict';
import { DshAgent } from '../src/dsh.mjs';
import { MAX_STEER_FRAME_BYTES } from '../src/dsh/turn.mjs';
import { MAX_FRAME_BYTES } from '../src/dsh-bridge/framer.mjs';
import { after } from 'node:test';
import { describe, it } from './dsh-node-gate.mjs';
import {
  flush, newAgent, startAgent, waitFor, acknowledge, event, settle,
} from './dsh-agent-fixture.mjs';

describe('DshAgent turn and wait state machine', () => {
  it('keeps a synchronous steer acknowledgement, prepends history and notice, and starts wait', async () => {
    const context = newAgent({ childOptions: { synchronousAck: true } });
    const { agent, store } = context;
    const child = await startAgent(context);
    agent.turnNotice = '[daemon notice] stopped';
    assert.equal(agent.tryStartBatch([{ text: 'current request', seq: 7, receiptId: 'r7' }]), true);
    await flush();
    const steer = child.requests('session/steer')[0];
    assert.equal(steer.params.sessionId, agent.threadId);
    assert.match(steer.params.contentBlocks[0].text, /earlier question/);
    assert.match(steer.params.contentBlocks[0].text, /\[daemon notice\] stopped\ncurrent request$/);
    assert.equal(agent.turn.submitSeq, 1);
    assert.deepEqual(agent.turn.items.map((item) => item.seq), [7]);
    assert.equal(agent.inflight.size, 0);
    assert.equal(child.requests('session/wait').length, 1);
    assert.equal(store.activities.filter((item) => item.kind === 'agent.turn_started').length, 1);
    // The acknowledgement alone is not consumption: the user/message event must
    // confirm it before the turn may settle as delivered.
    event(child, agent.threadId, 'user/message', { id: `sync-${steer.id}`, content: [] });
    await settle(child, agent);
    assert.deepEqual(context.successes, [{ inboundSeqs: [7], receiptIds: ['r7'] }]);
    assert.equal(context.failures.length, 0);
    assert.equal(store.global().syncedThroughSeq, 3);
    await agent.stop();
  });

  it('steers later input into the same turn, re-waits, and returns every item once', async () => {
    const context = newAgent();
    const { agent, child } = { ...context, child: await startAgent(context) };
    agent.deliver('first', 7, 'r-first');
    const first = child.requests('session/steer')[0];
    await acknowledge(child, first, 'msg-first');
    agent.deliverBatch([{ text: 'second', seq: 8, receiptId: 'r-second' }, { text: 'third', seq: 9 }]);
    const second = child.requests('session/steer')[1];
    await acknowledge(child, second, 'msg-second');
    assert.equal(child.requests('session/steer').length, 2);
    assert.ok(context.store.activities.some((item) => item.kind === 'agent.steered'));
    const firstWait = child.requests('session/wait')[0];
    child.respond(firstWait, {});
    await waitFor(() => child.requests('session/wait').length >= 2);
    await settle(child, agent);
    assert.deepEqual(context.successes, [{
      inboundSeqs: [7, 8, 9], receiptIds: ['r-first', 'r-second'],
    }]);
    assert.equal(context.failures.length, 0);
    await agent.stop();
  });

  it('prepends the history once per session, not once per steer', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    // A fresh session needs the OpenViking history prefix. Both batches below are
    // written before the first one is acknowledged; the second must not carry
    // the same history again.
    assert.equal(agent.needsHistoryPrefix, true);
    agent.deliver('first', 7);
    agent.deliver('second', 8);
    const frames = child.requests('session/steer');
    assert.equal(frames.length, 2);
    assert.match(frames[0].params.contentBlocks[0].text, /earlier question/);
    assert.doesNotMatch(frames[1].params.contentBlocks[0].text, /earlier question/,
      'the history prefix was attached to a second frame of the same session');
    assert.equal(agent.needsHistoryPrefix, false);
    await agent.stop();
  });

  it('does not repeat the history prefix in a later frame of a split steer', async () => {
    // The frame bodies are megabytes wide, so the RPC budget is not what this
    // case is about: only the frame bound and the one-shot history prefix are.
    const context = newAgent({
      options: { controlTimeoutMs: 60_000 },
      childOptions: { silentMethods: ['session/wait'] },
    });
    const { agent } = context;
    const child = await startAgent(context);
    const size = 9_000_000;
    // Held by a restart in flight, so both batches queue and the drain merges
    // them into frames that must be split.
    agent.restarting = true;
    agent.deliver('a'.repeat(size), 7, 'r-7');
    agent.deliver('b'.repeat(size), 8, 'r-8');
    agent.restarting = false;
    agent._drain();
    await waitFor(() => child.requests('session/steer').length === 2, 5_000);
    const prefixed = child.requests('session/steer')
      .filter((frame) => /earlier question/.test(frame.params.contentBlocks[0].text));
    assert.equal(prefixed.length, 1, 'exactly one frame of the session carries the history');
    await agent.stop();
  });

  it('fails an acknowledged input that is never observed instead of settling it as delivered', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    agent.deliver('unobserved', 7, 'receipt-unobserved');
    const steer = child.requests('session/steer')[0];
    child.respond(steer, { messageId: 'never-observed' });
    await waitFor(() => child.requests('session/wait').length === 1);
    event(child, agent.threadId, 'turn/end', { turn: 1, reason: { kind: 'completed' } });
    for (let round = 1; round <= 5; round += 1) {
      const request = child.requests('session/wait').at(-1);
      child.respond(request, {});
      if (round < 5) await waitFor(() => child.requests('session/wait').length === round + 1);
    }
    await waitFor(() => !agent.turn)
      .catch(error => { throw new Error(`${error.message}: pending=${JSON.stringify([...agent.pendingRpc.keys()])} turn=${JSON.stringify(agent.turn)} responses=${JSON.stringify([...child.respondedIds])} writes=${JSON.stringify(child.sent())}`); });
    // One initial wait plus three retries; the fourth reply settles the turn.
    assert.equal(child.requests('session/wait').length, 5, JSON.stringify(child.sent()));
    // An acknowledged input nobody observed is unknown consumption: it must be
    // reported as failed and must not advance the acceptance cursor.
    assert.equal(context.successes.length, 0);
    assert.equal(context.failures.length, 1);
    assert.deepEqual(context.failures[0].inboundSeqs, [7]);
    assert.deepEqual(context.failures[0].receiptIds, ['receipt-unobserved']);
    const completed = context.store.activities.find((item) => item.kind === 'agent.turn_completed');
    assert.equal(completed.status, 'failed');
    assert.equal(completed.metadata.endReason, 'unobserved');
    assert.equal(completed.metadata.errorCode, 'DSH_INPUT_UNOBSERVED');
    assert.deepEqual(completed.metadata.unobservedMessageIds, ['never-observed']);
    // The cursor stops just before the unconfirmed input's journal row (3), so
    // the earlier rows already accepted by a previous turn (through 2) stay.
    assert.equal(context.store.global().syncedThroughSeq, 2);
    await agent.stop();
  });

  for (const [name, reason, expectedStatus, callback, errorMatch] of [
    ['completed', { kind: 'completed' }, 'completed', 'success', null],
    ['max-tokens', { kind: 'max-tokens' }, 'completed', 'success', null],
    ['locally cancelled aborted', { kind: 'aborted', reason: { kind: 'user' } }, 'interrupted', 'cancel', null],
    ['remote aborted', { kind: 'aborted', reason: { kind: 'timeout' } }, 'failed', 'failure', /aborted: timeout/],
    ['error', { kind: 'error', error: { message: 'provider exploded', code: 'UPSTREAM', status: 503 } }, 'failed', 'failure', /provider exploded/],
    ['blocked', { kind: 'blocked' }, 'failed', 'failure', /blocked/],
    ['interrupted', { kind: 'interrupted' }, 'failed', 'failure', /interrupted/],
  ]) {
    it(`maps ${name} to ${expectedStatus}`, async () => {
      const context = newAgent();
      const { agent } = context;
      const child = await startAgent(context);
      agent.deliver('map outcome', 7);
      await acknowledge(child, child.requests('session/steer')[0]);
      if (callback === 'cancel') assert.equal(agent.cancelTurn(), true);
      await settle(child, agent, reason);
      const completed = context.store.activities.find((item) => item.kind === 'agent.turn_completed');
      assert.equal(completed.status, expectedStatus, JSON.stringify({ completed, reason, activities: context.store.activities }));
      assert.equal(completed.metadata.endReason, reason.kind);
      if (reason.kind === 'error') {
        assert.equal(completed.metadata.errorCode, 'UPSTREAM');
        assert.equal(completed.metadata.status, 503);
      }
      if (callback === 'success' || callback === 'cancel') {
        assert.equal(context.successes.length, 1);
        assert.equal(context.failures.length, 0);
      } else {
        assert.equal(context.failures.length, 1);
        assert.match(context.failures[0].error, errorMatch);
      }
      await agent.stop();
    });
  }

  it('treats a wait without turn/end as completed and a rejected wait as failed', async () => {
    const first = newAgent();
    const child = await startAgent(first);
    first.agent.deliver('no terminal', 7);
    await acknowledge(child, child.requests('session/steer')[0]);
    await settle(child, first.agent);
    assert.equal(first.successes.length, 1);
    await first.agent.stop();

    const second = newAgent();
    const secondChild = await startAgent(second);
    second.agent.deliver('wait failure', 7);
    await acknowledge(secondChild, secondChild.requests('session/steer')[0]);
    secondChild.reject(secondChild.requests('session/wait')[0], -32603, 'agent failed before wait');
    await waitFor(() => second.failures.length === 1);
    assert.match(second.failures[0].error, /agent failed before wait/);
    await second.agent.stop();
  });

  it('does not open an autonomous turn while a settings change is being applied', async () => {
    // B3: a status of `running` that lands mid-select would open a turn on the
    // route this process still has, while `appliedModel` is about to become the
    // requested one. The turn waits for the boundary that follows.
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    const root = agent.threadId;
    agent.setModel('deepseek-v4-pro');
    // Hold the select open so `settingsUpdate` is in flight.
    const answer = child._answer.bind(child);
    let releaseSelect = null;
    child._answer = (frame) => {
      if (frame.method !== 'superagent/session/model/select' || releaseSelect) { answer(frame); return; }
      releaseSelect = () => answer(frame);
    };
    agent.deliver('switch first', 7);
    await waitFor(() => typeof releaseSelect === 'function', 2_000);
    assert.ok(agent.settingsUpdate, 'the select is in flight');
    // DSH reports the session running while the selection is still landing.
    child.feed({ jsonrpc: '2.0', method: 'session.status', params: { sessionId: root, status: 'running' } });
    await flush();
    assert.equal(agent.turn, null, 'no autonomous turn is opened mid-settings');
    releaseSelect();
    await waitFor(() => child.requests('session/steer').length === 1, 2_000);
    assert.equal(agent.appliedModel, 'deepseek-official/deepseek-v4-pro');
    await acknowledge(child, child.requests('session/steer')[0]);
    await settle(child, agent);
    await agent.stop();
  });

  it('tracks autonomous root work, includes steered input, and ignores child sessions', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    const root = agent.threadId;
    child.feed({ jsonrpc: '2.0', method: 'session.status', params: { sessionId: root, status: 'running' } });
    event(child, root, 'user/message', { id: 'goal-message', source: { kind: 'goal' }, content: [] });
    await flush();
    const started = context.store.activities.find((item) => item.kind === 'agent.turn_started');
    assert.equal(started.metadata.autonomous, true);
    assert.equal(started.metadata.origin, 'goal');
    child.feed({ jsonrpc: '2.0', method: 'session.status', params: { sessionId: 'child-session', status: 'running' } });
    event(child, 'child-session', 'assistant/message', { message: { content: [{ type: 'text', text: 'child-private' }] } });
    assert.equal(context.store.activities.some((item) => item.summary === 'child-private'), false);
    assert.equal(agent.turn.gen, 1);
    agent.deliver('human steer', 7);
    await acknowledge(child, child.requests('session/steer')[0]);
    await settle(child, agent);
    assert.deepEqual(context.successes, [{ inboundSeqs: [7] }]);
    const done = context.store.activities.find((item) => item.kind === 'agent.turn_completed');
    assert.equal(done.metadata.autonomous, true);
    assert.equal(done.metadata.origin, 'goal');
    await agent.stop();
  });

  it('cancels in place and interrupt abandons unacknowledged input without stale settlement', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    assert.equal(agent.cancelTurn(), false);
    agent.deliver('interrupt me', 7, 'receipt-7');
    await agent.interruptTurn();
    assert.deepEqual(context.abandoned, [{ inboundSeqs: [7], receiptIds: ['receipt-7'] }]);
    const cancel = child.requests('session/cancel')[0];
    assert.deepEqual(cancel.params, { sessionId: agent.threadId, keepInbox: false });
    assert.equal(agent.turnInProgress, false);
    await agent.whenIdle();
    await settle(child, agent, { kind: 'aborted', reason: { kind: 'user' } });
    assert.equal(context.failures.length, 0);
    await agent.stop();
  });
});

describe('DshAgent input terminal states', () => {
  it('requeues a steer whose acknowledgement times out instead of losing it', async () => {
    const context = newAgent({ childOptions: { silentMethods: ['session/steer'] } });
    const { agent } = context;
    const child = await startAgent(context);
    agent.deliver('never acknowledged', 7, 'receipt-timeout');
    const steer = child.requests('session/steer')[0];
    assert.ok(steer);
    // No ack: the steer RPC times out at controlTimeoutMs (300ms).
    await waitFor(() => agent.queue.flat().length === 1, 2_000);
    // The batch must not be reported as delivered, and it must survive as input.
    assert.equal(context.successes.length, 0);
    assert.equal(context.failures.length, 0);
    assert.equal(context.abandoned.length, 0);
    assert.deepEqual(agent.queue.flat().map((item) => item.seq), [7]);
    assert.ok(context.warnings.some((entry) => entry.event === 'dsh_steer_failed_recycling'));
    // The process was recycled for a fresh session so the replay cannot double-send.
    assert.equal(agent.forceFreshSession, true);
    await agent.stop();
  });

  it('fails a steer acknowledged without a message id instead of counting it delivered', async () => {
    const context = newAgent({ childOptions: { steerResult: {} } });
    const { agent } = context;
    const child = await startAgent(context);
    agent.deliver('no message id', 7, 'receipt-malformed');
    await waitFor(() => context.warnings.some((entry) => entry.event === 'dsh_steer_failed_recycling')
      || agent.queue.flat().length === 1, 2_000);
    assert.equal(context.successes.length, 0);
    // A response without a message id cannot be tracked to consumption: the
    // batch is held for a fresh session rather than settled as success.
    assert.deepEqual(agent.queue.flat().map((item) => item.seq), [7]);
    assert.ok(context.warnings.some((entry) => entry.event === 'dsh_steer_failed_recycling'));
    await agent.stop();
  });

  it('reports an input whose turn was replaced exactly once and ignores the late ack', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    agent.deliver('orphan me', 7, 'receipt-orphan');
    const steer = child.requests('session/steer')[0];
    // Replace the turn before the acknowledgement lands.
    await agent.interruptTurn();
    assert.deepEqual(context.abandoned, [{ inboundSeqs: [7], receiptIds: ['receipt-orphan'] }]);
    // The late acknowledgement must not add a second terminal state.
    child.respond(steer, { messageId: 'orphan-message' });
    await flush();
    assert.equal(context.failures.length, 0);
    assert.equal(context.successes.length, 0);
    assert.equal(context.abandoned.length, 1);
    assert.equal(agent.deliveries.size, 0);
    assert.equal(agent.inflight.size, 0);
    await agent.stop();
  });

  it('replays an unacknowledged steer after its turn failed, ignoring the late ack', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    agent.deliver('acked input', 7, 'receipt-acked');
    await acknowledge(child, child.requests('session/steer')[0], 'acked-message');
    // The second steer is written but the SDK never answers it.
    child.silentMethods.add('session/steer');
    agent.deliver('unacked input', 8, 'receipt-unacked');
    const lateSteer = child.requests('session/steer')[1];
    assert.ok(lateSteer);
    // The wait itself fails, ending the turn while the second steer is unacked.
    await waitFor(() => child.requests('session/wait').length === 1);
    child.reject(child.requests('session/wait')[0], -32603, 'wait broke');
    await waitFor(() => context.failures.length === 1);
    // Only the acknowledged input is reported: the unacknowledged one is not
    // counted as delivered, it is replayed into the next turn.
    assert.deepEqual(context.failures[0].inboundSeqs, [7]);
    child.silentMethods.delete('session/steer');
    await waitFor(() => child.requests('session/steer').length === 3);
    const replay = child.requests('session/steer')[2];
    assert.match(replay.params.contentBlocks[0].text, /unacked input/);
    // The stale acknowledgement arrives for the closed request: no second
    // terminal state, no duplicate item in the new turn.
    child.respond(lateSteer, { messageId: 'late-message' });
    await flush();
    assert.equal(context.successes.length, 0);
    assert.deepEqual(agent.turn.items.map((item) => item.seq), []);
    await agent.stop();
  });

  it('drops a cancel-cleared inbox without turning it into a turn failure', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    agent.deliver('cancelled input', 7, 'receipt-cancel');
    const steer = child.requests('session/steer')[0];
    child.respond(steer, { messageId: 'cancelled-message' });
    await flush();
    assert.equal(agent.cancelTurn(), true);
    await settle(child, agent, { kind: 'aborted', reason: { kind: 'user' } });
    // keepInbox:false deliberately drops the steer: no failure callback, and
    // the drop is recorded so the loss is visible.
    assert.equal(context.failures.length, 0);
    assert.equal(context.successes.length, 1);
    const completed = context.store.activities.find((item) => item.kind === 'agent.turn_completed');
    assert.equal(completed.status, 'interrupted');
    assert.deepEqual(completed.metadata.droppedInboundSeqs, [7]);
    await agent.stop();
  });

  it('fails input still queued at shutdown', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    child.silentMethods.add('session/steer');
    agent.deliver('accepted', 7, 'receipt-accepted');
    await waitFor(() => child.requests('session/steer').length === 1);
    await agent.stop();
    // Stopping mid-steer reports the batch exactly once, never as success.
    assert.equal(context.successes.length, 0);
    assert.equal(context.failures.length, 1);
    assert.deepEqual(context.failures[0].inboundSeqs, [7]);
    assert.equal(agent.deliveries.size, 0);
  });
});

describe('DshAgent acknowledgement ordering', () => {
  it('accepts the consume event before the acknowledgement without a phantom pending', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    agent.deliver('event first', 7);
    const steer = child.requests('session/steer')[0];
    // The user/message event arrives before the steer reply.
    event(child, agent.threadId, 'user/message', { id: 'early-message', content: [] });
    await flush();
    child.respond(steer, { messageId: 'early-message' });
    await flush();
    assert.equal(agent.turn.pendingMessageIds.size, 0);
    await settle(child, agent);
    assert.deepEqual(context.successes, [{ inboundSeqs: [7] }]);
    assert.equal(context.failures.length, 0);
    assert.equal(context.store.global().syncedThroughSeq, 3);
    await agent.stop();
  });

  it('keeps the acknowledgement-first order working', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    agent.deliver('ack first', 7);
    const steer = child.requests('session/steer')[0];
    child.respond(steer, { messageId: 'late-message' });
    await flush();
    assert.equal(agent.turn.pendingMessageIds.size, 1);
    event(child, agent.threadId, 'user/message', { id: 'late-message', content: [] });
    await flush();
    assert.equal(agent.turn.pendingMessageIds.size, 0);
    await settle(child, agent);
    assert.deepEqual(context.successes, [{ inboundSeqs: [7] }]);
    await agent.stop();
  });
});

describe('DshAgent backpressure and frame bounds', () => {
  it('applies the queue bound while stdin is backpressured', async () => {
    const context = newAgent({
      options: { maxQueuedItems: 1, maxQueueBatches: 1 },
      childOptions: { writeBlocked: true, silentMethods: ['session/steer'] },
    });
    const { agent } = context;
    const child = await startAgent(context);
    assert.ok(agent.writeBlocked, 'stdin reports backpressure');
    agent.deliver('first', 7);
    agent.deliver('second', 8);
    await flush();
    // The cap still applies during backpressure: the overflow is failed with a
    // callback instead of being buffered in the stream.
    assert.deepEqual(agent.queue.flat().map((item) => item.seq), [7]);
    assert.equal(child.requests('session/steer').length, 0);
    assert.equal(context.failures.length, 1);
    assert.deepEqual(context.failures[0].inboundSeqs, [8]);
    assert.match(context.failures[0].error, /queue is full/);
    await agent.stop();
  });

  it('keeps the steer frame bound equal to the bridge frame bound', () => {
    // The bridge drops a line past MAX_FRAME_BYTES and the daemon would retry the
    // resulting unanswered steer forever, so the write-side bound is the same
    // number, not a second opinion about it.
    assert.equal(MAX_STEER_FRAME_BYTES, MAX_FRAME_BYTES);
  });

  it('fails an input that cannot fit one frame instead of writing a dropped line', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    agent.restarting = true;
    agent.deliver('x'.repeat(MAX_STEER_FRAME_BYTES + 500_000), 7, 'r-7');
    agent.restarting = false;
    agent._drain();
    await flush();
    // The bridge would drop this line without answering it, so nothing is written.
    assert.equal(child.requests('session/steer').length, 0);
    assert.equal(context.failures.length, 1);
    assert.deepEqual(context.failures[0].inboundSeqs, [7]);
    assert.match(context.failures[0].error, new RegExp(`larger than one ${MAX_STEER_FRAME_BYTES}-byte steer frame`));
    const completed = context.store.activities.filter((item) => item.kind === 'agent.turn_completed').at(-1);
    assert.equal(completed.metadata.endReason, 'input-too-large');
    await agent.stop();
  });

  it('splits a merged queue into frames that stay inside the bridge limit', async () => {
    // The frame bodies are megabytes wide, so the RPC budget is not what this
    // case is about: only the frame bound is.
    const context = newAgent({
      options: { controlTimeoutMs: 60_000 },
      childOptions: { silentMethods: ['session/wait'] },
    });
    const { agent } = context;
    const child = await startAgent(context);
    // Two inputs that are each legal on their own, and whose merge is over the
    // 16 MB frame bound the bridge enforces by dropping the line.
    const size = 9_000_000;
    // Held by a restart in flight, so both batches queue without a reconnect
    // being started; the drain then merges the whole queue in one go.
    agent.restarting = true;
    agent.deliver('a'.repeat(size), 7, 'r-7');
    agent.deliver('b'.repeat(size), 8, 'r-8');
    assert.equal(agent.queue.length, 2);
    agent.restarting = false;
    agent._drain();
    await waitFor(() => child.requests('session/steer').length === 2, 5_000);
    const frames = child.requests('session/steer');
    // Every written frame is a line the bridge will accept.
    for (const frame of frames) {
      assert.ok(Buffer.byteLength(JSON.stringify(frame), 'utf8') < 16_000_000);
    }
    await acknowledge(child, frames[0], 'msg-7');
    await acknowledge(child, frames[1], 'msg-8');
    await settle(child, agent);
    assert.deepEqual(context.successes, [{ inboundSeqs: [7, 8], receiptIds: ['r-7', 'r-8'] }]);
    assert.equal(context.failures.length, 0);
    await agent.stop();
  });
});

describe('DshAgent mixed observed and unobserved settlement', () => {
  it('reports observed and never-observed input separately when the turn fails', async () => {
    const context = newAgent({ childOptions: { silentMethods: ['session/wait'] } });
    const { agent } = context;
    const child = await startAgent(context);
    agent.deliver('observed input', 7, 'r-observed');
    await acknowledge(child, child.requests('session/steer')[0], 'observed-message');
    agent.deliver('unobserved input', 8, 'r-unobserved');
    child.respond(child.requests('session/steer')[1], { messageId: 'unobserved-message' });
    await flush();
    assert.equal(agent.turn.pendingMessageIds.size, 1);
    // The turn itself fails on the provider while the second batch stays
    // acknowledged but unobserved.
    await settle(child, agent, { kind: 'error', error: { message: 'provider exploded' } });
    assert.deepEqual(
      context.failures.map((info) => info.inboundSeqs).sort((left, right) => left[0] - right[0]),
      [[7], [8]],
    );
    const turnFailure = context.failures.find((info) => info.inboundSeqs[0] === 7);
    assert.match(turnFailure.error, /provider exploded/);
    assert.deepEqual(turnFailure.receiptIds, ['r-observed']);
    const unobserved = context.failures.find((info) => info.inboundSeqs[0] === 8);
    assert.match(unobserved.error, /did not confirm consumption/);
    assert.deepEqual(unobserved.receiptIds, ['r-unobserved']);
    // Each input is reported exactly once.
    assert.equal(context.successes.length, 0);
    const completed = context.store.activities.filter((item) => item.kind === 'agent.turn_completed').at(-1);
    assert.equal(completed.status, 'failed');
    assert.equal(completed.metadata.endReason, 'error');
    assert.deepEqual(completed.metadata.unobservedInboundSeqs, [8]);
    assert.deepEqual(completed.metadata.unobservedMessageIds, ['unobserved-message']);
    await agent.stop();
  });

  it('settles the consumed batch as delivered when only another one was never observed', async () => {
    const context = newAgent();
    const { agent } = context;
    const child = await startAgent(context);
    agent.deliver('consumed input', 7, 'r-consumed');
    await acknowledge(child, child.requests('session/steer')[0], 'consumed-message');
    agent.deliver('unobserved input', 8, 'r-unobserved');
    child.respond(child.requests('session/steer')[1], { messageId: 'unobserved-message' });
    await flush();
    // The turn itself ends normally; only the second batch is never observed.
    await settle(child, agent);
    assert.deepEqual(context.successes, [{ inboundSeqs: [7], receiptIds: ['r-consumed'] }]);
    assert.equal(context.failures.length, 1);
    assert.deepEqual(context.failures[0].inboundSeqs, [8]);
    assert.match(context.failures[0].error, /did not confirm consumption/);
    const completed = context.store.activities.filter((item) => item.kind === 'agent.turn_completed').at(-1);
    assert.equal(completed.status, 'failed');
    assert.equal(completed.metadata.endReason, 'unobserved');
    assert.equal(completed.metadata.errorCode, 'DSH_INPUT_UNOBSERVED');
    assert.deepEqual(completed.metadata.unobservedInboundSeqs, [8]);
    await agent.stop();
  });
});

describe('DshAgent settlement survives a failing store', () => {
  it('settles the turn when the acceptance cursor cannot be written', async () => {
    // The loop that settles a turn is started without being awaited, so an
    // exception out of the cursor write becomes an unhandled rejection and the
    // daemon exits. The cursor not advancing is the safe direction: the same
    // input is re-offered after a restart instead of being marked consumed.
    const context = newAgent();
    const { agent } = context;
    context.store.advanceOwnedSessionAcceptance = () => {
      throw new Error('database is locked');
    };
    const child = await startAgent(context);
    agent.deliver('consumed input', 7, 'r-consumed');
    await acknowledge(child, child.requests('session/steer')[0], 'consumed-message');
    await settle(child, agent);
    // The turn still completed and reported its own outcome.
    assert.deepEqual(context.successes, [{ inboundSeqs: [7], receiptIds: ['r-consumed'] }]);
    const completed = context.store.activities.filter((item) => item.kind === 'agent.turn_completed').at(-1);
    assert.equal(completed.status, 'completed');
    assert.ok(context.warnings.some((warning) => warning.event === 'dsh_session_acceptance_failed'),
      `the failed write is reported: ${JSON.stringify(context.warnings)}`);
    await agent.stop();
  });
});

describe('frame bounds across the two halves', () => {
  it('shares one contract between the bridge and the daemon transport', async () => {
  // A frame the bridge refuses but the daemon would accept is a request dropped
  // for no shared reason, so the two numbers are compared as constants rather
  // than against a literal that would keep passing after one side changed.
  const { MAX_FRAME_BYTES } = await import('../src/dsh-bridge/protocol.mjs');
  const { MAX_LINE_BYTES } = await import('../src/dsh/transport.mjs');
  assert.equal(MAX_FRAME_BYTES, MAX_LINE_BYTES);
});
})
