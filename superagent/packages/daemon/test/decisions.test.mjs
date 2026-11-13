// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { DecisionPolicy, warnIfSilenceInert, warnIfStagedRepliesInert } from '../src/decisions.mjs';
import { Harness } from '../src/harness.mjs';
import { RUNTIME_IDS, getRuntimeDefinition } from '../src/runtime-catalog.mjs';
import { Store } from '../src/store.mjs';
import { chatEvent, mailEvent } from './event-fixture.mjs';

const quiet = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

// A real Store and Harness; the runtime only records what it is handed.
function setup(t, { runtime = 'codex', explicitSilence = true, decisionReminderLimit = 3, notifyFailure = null } = {}) {
  const store = new Store(':memory:', quiet);
  store.initializeCognition(runtime, 'token');
  const submitted = [];
  const harness = new Harness({
    config: {}, store, logger: quiet, recordActivity() {},
    runtime: {
      snapshot: () => ({ session: { connected: true }, work: { turnInProgress: false } }),
      submit: (items) => { submitted.push(...items); return { accepted: true }; },
    },
  });
  const activities = [];
  const logs = [];
  const state = { stopping: false };
  const policy = new DecisionPolicy({
    store, harness,
    logger: { ...quiet, info: (message, fields) => logs.push({ message, ...fields }) },
    recordActivity: (kind, fields) => activities.push({ kind, ...fields }),
    explicitSilence,
    decisionReminderLimit,
    notifyFailure,
    isStopping: () => state.stopping,
  });
  t.after(() => { harness.stop(); store.close(); });
  const ingest = (fields) => store.ingest(chatEvent({ chatType: 'p2p', ...fields })).inbound;
  const silence = (row) => store.recordDecision({ row: store.db.prepare('SELECT * FROM connector_events WHERE seq = ?').get(row.seq),
    outcome: 'silent' });
  const settlement = () => logs.filter((line) => line.message === 'decision_settlement').at(-1);
  const turn = async (inboundSeqs) => {
    policy.onTurnSucceeded({ inboundSeqs });
    await harness.ingressBarrier;
    // A group line waits out the burst window otherwise.
    harness.flushBufferedIngress();
  };
  const stopped = async (inboundSeqs) => {
    policy.onTurnStopped({ inboundSeqs });
    await harness.ingressBarrier;
    harness.flushBufferedIngress();
  };
  return { store, policy, submitted, activities, logs, state, ingest, silence, settlement, turn, stopped };
}

describe('turn-end decision settlement', () => {
  it('leaves a decided scope alone', async (t) => {
    const { activities, submitted, ingest, silence, settlement, turn } = setup(t);
    const row = ingest({ messageId: 'om_decided123456' });
    silence(row);
    await turn([row.seq]);
    assert.deepEqual(activities, []);
    assert.deepEqual(submitted, []);
    assert.deepEqual(settlement(), { message: 'decision_settlement', delivered: 0, silent: 1, superseded: 0,
      expected_unsettled: 0, optional_unsettled: 0, nudged: 0 });
  });

  it('reminds an undecided direct message three times with its original event= and seq, then stops', async (t) => {
    const { activities, submitted, logs, ingest, turn } = setup(t);
    const row = ingest({ messageId: 'om_forgot1234567', chatId: 'oc_p2p12345678' });
    await turn([row.seq, null]);
    for (let reminder = 2; reminder <= 3; reminder += 1) await turn([row.seq]);
    assert.deepEqual(activities.map(({ kind, status, inboundSeq, metadata }) => [kind, status, inboundSeq, metadata]),
      Array.from({ length: 3 }, () => ['agent.decision_missing', 'NUDGED', row.seq, { expectation: 'expected', runtime: 'codex' }]));
    assert.equal(submitted.length, 3);
    for (const [index, item] of submitted.entries()) {
      assert.equal(item.seq, row.seq);
      assert.equal(item.receiptId, undefined);
      assert.match(item.text, /^\[chat=oc_p2p12345678 source=lark msg=om_forgot1234567 event=message:om_forgot1234567 time=\S+ from=\S+ undecided=1\] hello$/);
      assert.equal(logs.filter((line) => line.message === 'decision_nudged')[index].reminder, index + 1);
    }
    assert.ok(logs.filter((line) => line.message === 'decision_nudged').every((line) => line.seq === row.seq));
    assert.doesNotMatch(JSON.stringify(logs), /oc_p2p|om_forgot/);

    // The third reminder turn checks the same seq again but adds no fourth.
    await turn([row.seq]);
    assert.equal(submitted.length, 3);
    assert.deepEqual(activities.map(({ status }) => status), ['NUDGED', 'NUDGED', 'NUDGED', 'MISSING']);
    await turn([]);
    assert.equal(activities.length, 4);
  });

  it('honors a zero reminder limit', async (t) => {
    const { activities, submitted, ingest, turn } = setup(t, { decisionReminderLimit: 0 });
    const row = ingest({ messageId: 'om_noReminder123' });
    await turn([row.seq]);
    assert.deepEqual(submitted, []);
    assert.deepEqual(activities.map(({ status }) => status), ['MISSING']);
  });

  it('lets one decision on the newest input cover a burst, but not one on an older input', async (t) => {
    const { activities, ingest, silence, settlement, turn } = setup(t);
    const burst = ['om_burstA1234567', 'om_burstB1234567', 'om_burstC1234567']
      .map((messageId) => ingest({ messageId, chatId: 'oc_group12345678', chatType: 'group' }));
    silence(burst[2]);
    await turn(burst.map((row) => row.seq));
    assert.equal(settlement().silent, 1);

    const direct = ['om_directA123456', 'om_directB123456'].map((messageId) => ingest({ messageId, chatId: 'oc_p2pB12345678' }));
    silence(direct[0]);
    await turn(direct.map((row) => row.seq));
    assert.deepEqual(activities.map(({ status, inboundSeq }) => [status, inboundSeq]), [['NUDGED', direct[1].seq]]);
  });

  it('skips a scope with a newer input, which its own turn will check', async (t) => {
    const { activities, submitted, ingest, settlement, turn } = setup(t);
    const first = ingest({ messageId: 'om_firstA1234567' });
    ingest({ messageId: 'om_secondB123456' });
    await turn([first.seq]);
    assert.equal(settlement().superseded, 1);
    assert.deepEqual([activities, submitted], [[], []]);
  });

  it('expects a decision on every chat message, group and mention alike, never on mail', async (t) => {
    const { store, activities, submitted, ingest, settlement, turn } = setup(t);
    // One of the bot's own messages, so a quote of it reads reply_to=me.
    const outbox = store.reserveOutbox({ connector: 'lark', action: 'message.send', idempotencyKey: 'k1', payload: {}, caller: 'agent',
      chatId: 'oc_groupQ1234567' });
    store.settleOutbox(outbox.id, 'SENT', { result: { message_id: 'om_botSaid123456' } });
    const card = ingest({ messageId: 'ca_answer1234567', kind: 'message.action', messageType: 'card_action', chatId: 'oc_cards12345678', chatType: 'group' });
    const quote = ingest({ messageId: 'om_quoteMe123456', chatId: 'oc_groupQ1234567', chatType: 'group', parentId: 'om_botSaid123456' });
    const group = ingest({ messageId: 'om_ambient123456', chatId: 'oc_ambient123456', chatType: 'group' });
    const mail = store.ingest(mailEvent({ uid: 7 })).inbound;
    await turn([card.seq, quote.seq, group.seq, mail.seq]);
    assert.deepEqual(activities.map(({ inboundSeq }) => inboundSeq), [card.seq, quote.seq, group.seq]);
    const line = (messageId) => submitted.find((item) => item.text.includes(` msg=${messageId} `)).text;
    assert.match(line('om_quoteMe123456'), / reply_to=me undecided=1\]/);
    assert.match(line('ca_answer1234567'), / reply_to=me undecided=1\]/);
    assert.match(line('om_ambient123456'), /msg=om_ambient123456 [^\]]* undecided=1\]/);
    assert.deepEqual([settlement().expected_unsettled, settlement().optional_unsettled, settlement().nudged], [3, 1, 3]);
  });

  it('reminds about only the newest line of an undecided group burst', async (t) => {
    const { activities, submitted, ingest, settlement, turn } = setup(t);
    const burst = ['om_gburstA123456', 'om_gburstB123456', 'om_gburstC123456']
      .map((messageId) => ingest({ messageId, chatId: 'oc_gburst1234567', chatType: 'group' }));
    await turn(burst.map((row) => row.seq));
    assert.deepEqual(activities.map(({ status, inboundSeq }) => [status, inboundSeq]), [['NUDGED', burst[2].seq]]);
    assert.deepEqual(submitted.map((item) => item.text.includes(' msg=om_gburstC123456 ')), [true]);
    assert.equal(settlement().nudged, 1);
  });

  it('does nothing for Volc or while the daemon stops', async (t) => {
    const volc = setup(t, { runtime: 'volc' });
    const row = volc.ingest({ messageId: 'om_volc12345678' });
    await volc.turn([row.seq]);
    assert.deepEqual([volc.activities, volc.submitted, volc.logs], [[], [], []]);

    const stopping = setup(t);
    stopping.state.stopping = true;
    const other = stopping.ingest({ messageId: 'om_stopping123456' });
    await stopping.turn([other.seq]);
    assert.deepEqual([stopping.activities, stopping.submitted], [[], []]);
  });

  it('ignores null seqs and daemon-handled inputs', async (t) => {
    const { store, activities, submitted, turn } = setup(t);
    const ignored = store.ingest(chatEvent({ chatType: 'p2p', messageId: 'om_slash12345678' }), 'slash_command').inbound;
    await turn([null, undefined, ignored.seq]);
    assert.deepEqual([activities, submitted], [[], []]);
  });
});

describe('explicit silence on a runtime without core agent tools', () => {
  const capture = () => {
    const warnings = [];
    return { warnings, logger: { ...quiet, warn: (message, fields) => warnings.push({ message, ...fields }) } };
  };

  it('warns once per call that explicit silence is not applied, naming the runtime', () => {
    const { warnings, logger } = capture();
    assert.equal(warnIfSilenceInert({ explicitSilence: true }, 'volc', logger), true);
    assert.equal(warnings.length, 1);
    assert.equal(warnings[0].message, 'explicit_silence_not_applied');
    assert.equal(warnings[0].runtime, 'volc');
    assert.match(warnings[0].reason, /explicit silence is not applied for this runtime/);
  });

  it('stays quiet on every runtime that has core agent tools', () => {
    const { warnings, logger } = capture();
    for (const id of RUNTIME_IDS.filter((runtime) => getRuntimeDefinition(runtime).capabilities.agentTools === 'core')) {
      assert.equal(warnIfSilenceInert({ explicitSilence: true }, id, logger), false, id);
    }
    assert.deepEqual(warnings, []);
  });

  it('says the same of staged replies, which only the Core-tool runtimes deliver', () => {
    const { warnings, logger } = capture();
    assert.equal(warnIfStagedRepliesInert({ stagedReplies: true }, 'volc', logger), true);
    assert.deepEqual(warnings.map(({ message, runtime }) => [message, runtime]), [['staged_replies_not_applied', 'volc']]);
    assert.match(warnings[0].reason, /staged replies is not applied for this runtime/);
    for (const id of RUNTIME_IDS) {
      const core = getRuntimeDefinition(id).capabilities.agentTools === 'core';
      assert.equal(warnIfStagedRepliesInert({ stagedReplies: true }, id, logger), !core, id);
      assert.equal(warnIfStagedRepliesInert({ stagedReplies: false }, id, logger), false, id);
    }
  });

  it('stays quiet when the switch is off, whatever the runtime', () => {
    const { warnings, logger } = capture();
    for (const id of RUNTIME_IDS) assert.equal(warnIfSilenceInert({ explicitSilence: false }, id, logger), false, id);
    assert.equal(warnIfSilenceInert({}, 'volc', logger), false);
    assert.deepEqual(warnings, []);
  });
});

describe('loop-stopped turn recovery', () => {
  it('brings back once, with its seq, every input the stopped turn left undecided, then tells the sender', async (t) => {
    const notices = [];
    const { store, submitted, activities, logs, ingest, silence, stopped } = setup(t, {
      notifyFailure: async (info) => { notices.push(info); },
    });
    const looped = ingest({ messageId: 'om_looped1234567', chatId: 'oc_loop12345678' });
    silence(looped);
    const waiting = ingest({ messageId: 'om_waiting123456', chatId: 'oc_wait12345678' });
    const older = ingest({ messageId: 'om_older12345678', chatId: 'oc_later12345678' });
    ingest({ messageId: 'om_newer12345678', chatId: 'oc_later12345678' });
    // Mail too: a stopped turn is no evidence anyone looked at it.
    const mail = store.ingest(mailEvent({ uid: 9 })).inbound;

    await stopped([looped.seq, waiting.seq, older.seq, mail.seq]);
    assert.deepEqual(submitted.map((item) => item.seq), [waiting.seq, mail.seq]);
    assert.match(submitted[0].text, /msg=om_waiting123456 event=message:om_waiting123456 [^\]]* undecided=1\] hello$/);
    assert.deepEqual(logs.filter((line) => line.message === 'stopped_turn_settlement').at(-1),
      { message: 'stopped_turn_settlement', decided: 1, superseded: 1, redelivered: 2, missing: 0 });
    assert.deepEqual(activities.map(({ status, inboundSeq, metadata }) => [status, inboundSeq, metadata.expectation]),
      [['NUDGED', waiting.seq, 'loop_stopped'], ['NUDGED', mail.seq, 'loop_stopped']]);

    // Held by a stopped turn again: not shown a third time, its sender is told.
    await stopped([waiting.seq]);
    assert.equal(submitted.length, 2);
    assert.deepEqual(notices.map((info) => info.inboundSeqs), [[waiting.seq]]);
    assert.match(notices[0].error, /中止/);
    assert.deepEqual(activities.at(-1).status, 'MISSING');
  });

  it('brings a line back untagged on an instance without explicitSilence, where turn ends check nothing', async (t) => {
    const { submitted, ingest, turn, stopped } = setup(t, { explicitSilence: false });
    const row = ingest({ messageId: 'om_plain12345678', chatId: 'oc_plain1234567' });
    await turn([row.seq]);
    assert.deepEqual(submitted, [], 'no reminder after a normal turn');
    await stopped([row.seq]);
    assert.equal(submitted.length, 1);
    assert.equal(submitted[0].seq, row.seq);
    assert.doesNotMatch(submitted[0].text, /undecided=/);
  });
});
