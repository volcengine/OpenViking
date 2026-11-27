// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { AgentRuntime } from '../src/agent-runtime.mjs';
import { startMockEnv, textMessage } from './mock-env.mjs';

function readySeqs(env) {
  return env.daemon.store.db.prepare('SELECT seq FROM inbox_pending ORDER BY id').all().map((row) => row.seq);
}

async function acceptedEvent(env, message) {
  const rows = () => env.daemon.store.acceptedEvents(message.header.event_id);
  await env.waitFor(() => rows().length === 1);
  return rows()[0];
}

function useMockArk(t, runtime, deliverBatch = () => true) {
  t.mock.method(runtime.factories, 'volc', ({ model }) => ({
    model,
    state: { connected: true, queued: 0 },
    sessionDescriptor: { sessionId: 'ark-test', policyHash: 'ark-test' },
    async start() {},
    async stop() {},
    deliverBatch,
  }));
}

async function syncedHistory(env) {
  const { agentRuntime, ovContext, store } = env.daemon;
  await agentRuntime.whenIdle();
  await ovContext.checkpoint(store.getCognitionState().epoch);
  return [...ovContext.client.messages.values()].flat().map(({ content }) => content).join('\n');
}

describe('inbox scheduler end to end', () => {
  for (const scheduler of ['inbox', 'workers']) {
    it(`B1 resumes history sync after restarting from ${scheduler} in legacy mode`, async (t) => {
      const first = await startMockEnv({ config: { scheduler } });
      const roster = Promise.withResolvers();
      const stranded = textMessage({ messageId: 'om-stranded', text: 'stranded before restart' });
      let restarted;
      try {
        if (scheduler === 'workers') {
          const committed = textMessage({ messageId: 'om-ready-before-legacy', text: 'ready before restart' });
          const publish = t.mock.method(first.daemon.inbox, 'publish', () => {});
          first.receive(committed);
          const { seq: readySeq } = await acceptedEvent(first, committed);
          await first.daemon.harness.ingressBarrier;
          assert.deepEqual(readySeqs(first), [readySeq]);
          publish.mock.restore();
        }

        t.mock.method(first.daemon.harness.roster, 'warm', () => roster.promise);
        first.receive(stranded);
        const { seq } = await acceptedEvent(first, stranded);
        assert.deepEqual(first.daemon.store.pendingInboxInputs().map((row) => row.seq), [seq]);
        await first.daemon.shutdown('restart', 0);

        restarted = await startMockEnv({ config: { scheduler: 'legacy', databasePath: first.config.databasePath } });
        restarted.receive(textMessage({ messageId: 'om-after-legacy-restart', text: 'new legacy history' }));
        await restarted.waitForCodexInput(/msg=om-after-legacy-restart /);
        const history = await syncedHistory(restarted);
        assert.match(history, /new legacy history/);
        assert.match(history, /stranded before restart/);
        const inputs = restarted.codexInput().map(({ text }) => text).join('\n');
        assert.match(inputs, /msg=om-stranded /);
        if (scheduler === 'workers') {
          assert.match(history, /ready before restart/);
          assert.match(inputs, /msg=om-ready-before-legacy /);
        }
        assert.deepEqual(restarted.daemon.store.pendingInboxInputs(), []);
        assert.deepEqual(readySeqs(restarted), []);
        assert.ok(!restarted.daemon.actions.tools().some((tool) => tool.name === 'message_check'));
      } finally {
        roster.resolve();
        try { await restarted?.stop(); } finally { await first.stop(); }
      }
    });
  }

  it('B2 drains Ark inputs by scope and retries rejected batches automatically', async (t) => {
    const env = await startMockEnv({ config: { scheduler: 'inbox' } });
    const { agentRuntime, harness } = env.daemon;
    const roster = Promise.withResolvers();
    const first = textMessage({ messageId: 'om-ark-a', text: 'first Ark input', chatId: 'oc-a' });
    const second = textMessage({ messageId: 'om-ark-b', text: 'second Ark input', chatId: 'oc-b' });
    const delivered = [];
    const release = agentRuntime.holdAdmission();
    let allowSecond = false;
    let rejectedSecond = 0;
    try {
      t.mock.method(harness.roster, 'warm', () => roster.promise);
      useMockArk(t, agentRuntime, (items) => {
        if (!allowSecond && items.some(({ text }) => text.includes('msg=om-ark-b '))) {
          rejectedSecond += 1;
          return false;
        }
        delivered.push(items.map(({ seq }) => seq));
        return true;
      });

      env.receive(first);
      const { seq: firstSeq } = await acceptedEvent(env, first);
      env.receive(second);
      const { seq: secondSeq } = await acceptedEvent(env, second);
      await agentRuntime.transition({ kind: 'select-runtime', runtime: 'volc', persistPreference: false });
      roster.resolve();
      await harness.ingressBarrier;
      assert.deepEqual(readySeqs(env), [firstSeq, secondSeq], 'the transition hold keeps both rows');
      assert.deepEqual(delivered, []);

      release();
      await env.waitFor(() => rejectedSecond > 0 || delivered.length > 1, 3_000);
      assert.deepEqual(delivered, [[firstSeq]]);
      assert.deepEqual(readySeqs(env), [secondSeq], 'only the accepted scope is removed');
      allowSecond = true;
      await env.waitFor(() => delivered.length === 2, 2_500);
      assert.deepEqual(delivered, [[firstSeq], [secondSeq]]);
      assert.deepEqual(readySeqs(env), []);
    } finally {
      roster.resolve();
      release();
      await env.stop();
    }
  });

  it('B3 retries a rolled-back ready write and syncs following history in the same process', async (t) => {
    const env = await startMockEnv({ config: { scheduler: 'inbox' } });
    const { harness, inbox, store } = env.daemon;
    const failed = textMessage({ messageId: 'om-rollback', text: 'retry after rollback' });
    const persist = inbox.persist.bind(inbox);
    let rolledBack = false;
    try {
      const writes = t.mock.method(inbox, 'persist', (item, meta) => {
        const entry = persist(item, meta);
        if (!rolledBack) {
          rolledBack = true;
          throw new Error('ready transaction rolled back');
        }
        return entry;
      });
      env.receive(failed);
      const { seq } = await acceptedEvent(env, failed);
      await harness.ingressBarrier;
      assert.deepEqual(store.pendingInboxInputs().map((row) => row.seq), [seq]);
      assert.deepEqual(readySeqs(env), []);

      env.receive(textMessage({ messageId: 'om-after-rollback', text: 'history after rollback' }));
      await env.waitForCodexInput(/msg=om-after-rollback /);
      await env.waitFor(() => writes.mock.calls.filter(({ arguments: [item] }) => item.seq === seq).length > 1, 2_500);
      await env.waitForCodexInput(/msg=om-rollback /);
      const history = await syncedHistory(env);
      assert.match(history, /retry after rollback/);
      assert.match(history, /history after rollback/);
      assert.deepEqual(store.pendingInboxInputs(), []);
    } finally {
      await env.stop();
    }
  });

  it('settles repeated preparation failures so later history can sync', async (t) => {
    const env = await startMockEnv({ config: { scheduler: 'inbox' } });
    const { harness, store } = env.daemon;
    const failed = textMessage({ messageId: 'om-bad-prep', text: 'repeated preparation failure' });
    const prepare = harness.prepareInput.bind(harness);
    try {
      t.mock.method(harness, 'prepareInput', (event, inbound) => {
        if (event.messageId === 'om-bad-prep') throw new Error('cannot prepare input');
        return prepare(event, inbound);
      });
      env.receive(failed);
      const { seq } = await acceptedEvent(env, failed);
      await harness.ingressBarrier;
      assert.deepEqual(store.pendingInboxInputs().map((row) => row.seq), [seq]);

      env.receive(textMessage({ messageId: 'om-after-bad-prep', text: 'history after repeated failure' }));
      await env.waitForCodexInput(/msg=om-after-bad-prep /);
      await env.waitFor(() => store.pendingInboxInputs().length === 0);
      assert.match(await syncedHistory(env), /history after repeated failure/);
      assert.doesNotMatch(env.codexInput().map(({ text }) => text).join('\n'), /msg=om-bad-prep /);
    } finally {
      await env.stop();
    }
  });

  it('B4 routes an input queued behind /runtime to the new Core inbox', async (t) => {
    const env = await startMockEnv({ config: { scheduler: 'inbox' } });
    const { agentRuntime, harness, inbox } = env.daemon;
    let switching = false;
    const proceed = Promise.withResolvers();
    try {
      useMockArk(t, agentRuntime);
      await agentRuntime.transition({ kind: 'select-runtime', runtime: 'volc', persistPreference: false });
      t.mock.method(agentRuntime, 'refreshPolicy', () => {
        switching = true;
        return proceed.promise;
      });
      const schedule = t.mock.method(inbox, 'schedule', () => {});
      env.receive(textMessage({ messageId: 'om-switch-to-core', text: '/runtime codex' }));
      await env.waitFor(() => switching);

      const message = textMessage({ messageId: 'om-after-core-switch', text: 'wait for the Core inbox' });
      env.receive(message);
      const { seq } = await acceptedEvent(env, message);
      assert.equal(agentRuntime.runtime, 'volc');
      assert.deepEqual(env.daemon.store.pendingInboxInputs().map((row) => row.seq), [seq]);
      proceed.resolve();
      await harness.ingressBarrier;
      assert.equal(agentRuntime.runtime, 'codex');
      assert.deepEqual(readySeqs(env), [seq], 'the new Core runtime receives the input through the inbox');
      assert.deepEqual(env.daemon.store.pendingInboxInputs(), []);
      assert.deepEqual(env.codexInput(), []);

      schedule.mock.restore();
      inbox.onAvailable();
      await env.waitForCodexInput(/msg=om-after-core-switch /);
      assert.deepEqual(readySeqs(env), []);
    } finally {
      proceed.resolve();
      await env.stop();
    }
  });

  // The fake codex's 'inbox' behavior: a turn whose input says LONG stays open,
  // an inbox notice makes it call message_check, and the line that follows gets
  // one reply that ends the turn.
  it('holds an input that arrives mid-turn, notices it without its text, and delivers it on message_check', async () => {
    const env = await startMockEnv({ codex: { behavior: 'inbox' }, config: { scheduler: 'inbox' } });
    try {
      env.receive(textMessage({ messageId: 'om-long', text: 'LONG task for the owner', chatId: 'oc-a' }));
      await env.waitForCodexInput(/om-long/);
      env.receive(textMessage({ messageId: 'om-quick', text: 'a quick question', chatId: 'oc-b', senderId: 'ou-other' }));
      await env.waitForReply(/mock reply/, 15_000);

      const input = env.codexInput();
      const steers = input.filter((entry) => entry.method === 'turn/steer');
      assert.equal(input.filter((entry) => entry.method === 'turn/start').length, 1, 'no second turn');
      assert.match(steers[0].text, /^\[daemon inbox\] waiting=1 chats=1; new since the last notice:\n- chat=oc-b source=lark type=p2p waiting=1/);
      assert.doesNotMatch(steers[0].text, /quick question|om-quick/);
      assert.match(input.find((entry) => entry.method === 'tool-result').text, /^Delivered 1 waiting input\(s\), oldest first\. They follow as your next input/);
      assert.match(steers[1].text, /msg=om-quick .*a quick question/);
      assert.equal(env.activities('inbox.notified').length, 1);
      assert.deepEqual(env.activities('inbox.delivered').map((row) => row.metadata.reason), ['wake', 'check']);
      assert.equal(env.daemon.store.db.prepare('SELECT COUNT(*) AS n FROM inbox_pending').get().n, 0);
    } finally {
      await env.stop();
    }
  });

  it('legacy steers the same input into the running turn at once, and has no inbox', async () => {
    const env = await startMockEnv({ codex: { behavior: 'inbox' } });
    try {
      env.receive(textMessage({ messageId: 'om-long', text: 'LONG task for the owner', chatId: 'oc-a' }));
      await env.waitForCodexInput(/om-long/);
      env.receive(textMessage({ messageId: 'om-quick', text: 'a quick question', chatId: 'oc-b', senderId: 'ou-other' }));
      await env.waitForReply(/mock reply/, 15_000);
      const steers = env.codexInput().filter((entry) => entry.method === 'turn/steer');
      assert.equal(steers.length, 1);
      assert.match(steers[0].text, /msg=om-quick .*a quick question/);
      assert.equal(env.daemon.store.db.prepare("SELECT COUNT(*) AS n FROM sqlite_master WHERE name = 'inbox_pending'").get().n, 0);
      assert.ok(!env.daemon.actions.tools().some((tool) => tool.name === 'message_check'));
    } finally {
      await env.stop();
    }
  });

  for (const scheduler of ['inbox', 'workers']) {
    it(`recovers accepted inputs in order after stopping during preparation under ${scheduler}`, async () => {
      const first = await startMockEnv({ config: { scheduler } });
      const roster = Promise.withResolvers();
      let warming = false;
      const messages = [
        textMessage({ messageId: 'om-preparing', text: 'first waiting message' }),
        textMessage({ messageId: 'om-queued', text: 'second waiting message' }),
      ];
      let restarted;
      try {
        first.daemon.harness.roster.warm = () => {
          warming = true;
          return roster.promise;
        };
        first.receive(messages[0]);
        await first.waitFor(() => warming);
        first.receive(messages[1]);
        const accepted = (env) => messages.map(({ header }) => env.daemon.store.acceptedEvents(header.event_id)[0]);
        await first.waitFor(() => accepted(first).every(Boolean));
        const seqs = accepted(first).map((row) => row.seq);
        assert.deepEqual(first.daemon.inbox.check(), { delivered: 0, waiting: 0 });
        assert.equal(first.daemon.inbox.notify(), false);
        assert.deepEqual(first.codexInput(), []);

        await first.daemon.shutdown('restart', 0);
        restarted = await startMockEnv({ config: { scheduler, databasePath: first.config.databasePath } });
        await restarted.waitForCodexInput(/msg=om-queued /);
        assert.deepEqual(restarted.activities('inbox.delivered').flatMap((row) => row.metadata.inboundSeqs), seqs);

        for (const message of messages) restarted.receive(message);
        restarted.receive(textMessage({ messageId: 'om-new', text: 'new after restart' }));
        await restarted.waitForCodexInput(/msg=om-new /);
        assert.deepEqual(restarted.codexInput().flatMap(({ text }) =>
          [...text.matchAll(/msg=(om-preparing|om-queued|om-new) /g)].map((match) => match[1])),
        ['om-preparing', 'om-queued', 'om-new']);
        assert.deepEqual(accepted(restarted).map((row) => row.seq), seqs);
        assert.deepEqual(restarted.daemon.store.cognitionJournal()
          .filter((row) => row.kind === 'inbound' && seqs.includes(row.inboundSeq))
          .map((row) => row.inboundSeq), seqs);
      } finally {
        roster.resolve();
        try { await restarted?.stop(); } finally { await first.stop(); }
      }
    });
  }

  for (const stage of ['context preparation', 'ready transaction']) {
    it(`recovers on restart after ${stage} fails`, async (t) => {
      const first = await startMockEnv({ config: { scheduler: 'inbox' } });
      const { agentRuntime, inbox, harness, store } = first.daemon;
      const message = textMessage({ messageId: 'om-failed-prep', text: 'prepare again after restart' });
      let restarted;
      try {
        let failure;
        if (stage === 'context preparation') {
          failure = t.mock.method(agentRuntime, 'prepareInputContext', async () => { throw new Error('prepare failed'); });
        } else {
          const persist = inbox.persist.bind(inbox);
          failure = t.mock.method(inbox, 'persist', (item, meta) => {
            persist(item, meta);
            throw new Error('ready transaction failed');
          });
        }
        first.receive(message);
        const { seq } = await acceptedEvent(first, message);
        await harness.ingressBarrier;
        assert.deepEqual(store.pendingInboxInputs().map((row) => row.seq), [seq]);
        assert.deepEqual(readySeqs(first), []);
        assert.deepEqual(inbox.check(), { delivered: 0, waiting: 0 });
        assert.equal(inbox.notify(), false);
        assert.deepEqual(first.codexInput(), []);

        if (stage === 'context preparation') {
          await agentRuntime.transition({ kind: 'new-session' });
          const { epoch } = store.getCognitionState();
          await first.daemon.ovContext.checkpoint(epoch);
          for (const messages of first.daemon.ovContext.client.messages.values()) {
            assert.ok(messages.every(({ content }) => !content.includes('om-failed-prep')));
          }
        }

        await first.daemon.shutdown('restart', 0);
        failure.mock.restore();
        restarted = await startMockEnv({ config: { scheduler: 'inbox', databasePath: first.config.databasePath } });
        await restarted.waitForCodexInput(/msg=om-failed-prep /);
        assert.deepEqual(restarted.activities('inbox.delivered').flatMap((row) => row.metadata.inboundSeqs), [seq]);
        assert.deepEqual(restarted.daemon.store.pendingInboxInputs(), []);
      } finally {
        try { await restarted?.stop(); } finally { await first.stop(); }
      }
    });
  }

  it('recovers an input committed before publish and retains it until runtime acceptance', async (t) => {
    const first = await startMockEnv({ config: { scheduler: 'inbox' } });
    const message = textMessage({ messageId: 'om-committed', text: 'committed before publish' });
    let restarted;
    try {
      t.mock.method(first.daemon.inbox, 'publish', () => { throw new Error('stopped before publish'); });
      first.receive(message);
      const { seq } = await acceptedEvent(first, message);
      await first.daemon.harness.ingressBarrier;
      assert.deepEqual(first.daemon.store.pendingInboxInputs(), []);
      assert.deepEqual(readySeqs(first), [seq]);
      assert.deepEqual(first.daemon.inbox.check(), { delivered: 0, waiting: 0 });
      await first.daemon.shutdown('restart', 0);

      const submit = AgentRuntime.prototype.submit;
      let reject = true;
      let rejected = 0;
      t.mock.method(AgentRuntime.prototype, 'submit', function(items, options) {
        if (reject && items.some((item) => item.seq === seq)) {
          rejected += 1;
          return { accepted: false, reason: 'switching' };
        }
        return submit.call(this, items, options);
      });
      restarted = await startMockEnv({ config: { scheduler: 'inbox', databasePath: first.config.databasePath } });
      await restarted.waitFor(() => rejected > 0);
      assert.deepEqual(readySeqs(restarted), [seq]);
      assert.deepEqual(restarted.daemon.store.pendingInboxInputs(), []);
      assert.deepEqual(restarted.codexInput(), []);

      reject = false;
      await restarted.waitForCodexInput(/msg=om-committed /);
      assert.deepEqual(readySeqs(restarted), []);
      assert.deepEqual(restarted.activities('inbox.delivered').flatMap((row) => row.metadata.inboundSeqs), [seq]);
    } finally {
      try { await restarted?.stop(); } finally { await first.stop(); }
    }
  });

  it('drains an input accepted before switching to a runtime without Core tools', async (t) => {
    const env = await startMockEnv({ config: { scheduler: 'inbox' } });
    const { agentRuntime, inbox, harness, store } = env.daemon;
    const roster = Promise.withResolvers();
    const message = textMessage({ messageId: 'om-before-switch', text: 'accepted on Codex' });
    const delivered = [];
    let allowDelivery = false;
    try {
      t.mock.method(harness.roster, 'warm', () => roster.promise);
      useMockArk(t, agentRuntime, (items) => {
        if (!allowDelivery) return false;
        delivered.push(...items);
        return true;
      });

      env.receive(message);
      const { seq } = await acceptedEvent(env, message);
      assert.deepEqual(store.pendingInboxInputs().map((row) => row.seq), [seq]);
      const result = await agentRuntime.transition({ kind: 'select-runtime', runtime: 'volc', persistPreference: false });
      assert.equal(result.runtime, 'volc');
      assert.equal(inbox.usable(), false);
      roster.resolve();
      await harness.ingressBarrier;
      assert.deepEqual(store.pendingInboxInputs(), []);
      assert.deepEqual(readySeqs(env), [seq], 'a rejected drain keeps the ready row');
      assert.deepEqual(delivered, []);

      allowDelivery = true;
      harness.onAvailable();
      await env.waitFor(() => delivered.length === 1);
      assert.equal(delivered[0].seq, seq);
      assert.match(delivered[0].text, /msg=om-before-switch /);
      assert.deepEqual(readySeqs(env), []);
      assert.deepEqual(env.activities('inbox.delivered').map((row) => row.metadata.reason), ['drain']);

      const legacy = textMessage({ messageId: 'om-after-switch', text: 'accepted on Ark' });
      env.receive(legacy);
      const { seq: legacySeq } = await acceptedEvent(env, legacy);
      await harness.ingressBarrier;
      assert.deepEqual(delivered.map((item) => item.seq), [seq, legacySeq]);
      assert.deepEqual(store.pendingInboxInputs(), []);
      assert.deepEqual(readySeqs(env), []);
      assert.equal(env.activities('inbox.delivered').length, 1);
    } finally {
      roster.resolve();
      await env.stop();
    }
  });
});
