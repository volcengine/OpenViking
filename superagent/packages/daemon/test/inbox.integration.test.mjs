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

// The fake codex's 'inbox' behavior: a turn whose input says LONG stays open,
// an inbox notice makes it call message_check, and the line that follows gets
// one reply that ends the turn.
describe('inbox scheduler end to end', () => {
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

        failure.mock.restore();
        const healthy = textMessage({ messageId: 'om-healthy', text: 'continue while preparation waits' });
        first.receive(healthy);
        const { seq: healthySeq } = await acceptedEvent(first, healthy);
        await first.waitForCodexInput(/msg=om-healthy /);
        assert.ok(first.codexInput().every(({ text }) => !text.includes('om-failed-prep')));

        if (stage === 'context preparation') {
          await agentRuntime.transition({ kind: 'new-session' });
          const { epoch } = store.getCognitionState();
          await first.daemon.ovContext.checkpoint(epoch);
          for (const messages of first.daemon.ovContext.client.messages.values()) {
            assert.ok(messages.every(({ content }) => !content.includes('om-failed-prep')));
          }
        }

        await first.daemon.shutdown('restart', 0);
        restarted = await startMockEnv({ config: { scheduler: 'inbox', databasePath: first.config.databasePath } });
        await restarted.waitForCodexInput(/msg=om-failed-prep /);
        assert.deepEqual(restarted.activities('inbox.delivered').flatMap((row) => row.metadata.inboundSeqs), [healthySeq, seq]);
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
      t.mock.method(agentRuntime.factories, 'volc', ({ model }) => ({
        model,
        state: { connected: true, queued: 0 },
        sessionDescriptor: { sessionId: 'ark-test', policyHash: 'ark-test' },
        async start() {},
        async stop() {},
        deliverBatch(items) {
          if (!allowDelivery) return false;
          delivered.push(...items);
          return true;
        },
      }));

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
