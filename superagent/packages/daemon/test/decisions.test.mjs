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
function setup(t, { runtime = 'codex', explicitSilence = true, decisionReminderLimit = undefined, notifyFailure = null } = {}) {
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
  // Timers the policy starts, fired by hand.
  const timers = [];
  const policy = new DecisionPolicy({
    store, harness,
    logger: { ...quiet, info: (message, fields) => logs.push({ message, ...fields }) },
    recordActivity: (kind, fields) => activities.push({ kind, ...fields }),
    explicitSilence,
    decisionReminderLimit,
    notifyFailure,
    isStopping: () => state.stopping,
    blockedDelayMs: 60_000,
    setTimer: (fn, ms) => { const timer = { fn, ms, cleared: false }; timers.push(timer); return timer; },
    clearTimer: (timer) => { timer.cleared = true; },
  });
  t.after(() => { harness.stop(); store.close(); });
  const ingest = (fields) => store.ingest(chatEvent({ chatType: 'p2p', ...fields })).inbound;
  const raw = (row) => store.db.prepare('SELECT * FROM connector_events WHERE seq = ?').get(row.seq);
  const silence = (row) => store.recordDecision({ row: raw(row), outcome: 'silent' });
  const deliver = (row, covers = []) => {
    store.recordDecision({ row: raw(row), outcome: 'delivered' });
    for (const other of covers) store.recordDecision({ row: raw(other), outcome: 'delivered', coveredBy: raw(row).event_id });
  };
  const fire = async () => {
    const due = timers.filter((timer) => !timer.cleared && !timer.fired);
    for (const timer of due) { timer.fired = true; timer.fn(); }
    await harness.ingressBarrier;
    harness.flushBufferedIngress();
    return due.length;
  };
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
  return { store, policy, submitted, activities, logs, state, timers, ingest, silence, deliver, fire, settlement, turn, stopped };
}

describe('turn-end decision settlement', () => {
  it('leaves a decided input alone', async (t) => {
    const { activities, submitted, ingest, silence, settlement, turn } = setup(t);
    const row = ingest({ messageId: 'om_decided123456' });
    silence(row);
    await turn([row.seq]);
    assert.deepEqual(activities, []);
    assert.deepEqual(submitted, []);
    assert.deepEqual(settlement(), { message: 'decision_settlement', delivered: 0, silent: 1,
      expected_unsettled: 0, optional_unsettled: 0, nudged: 0, missing: 0 });
  });

  it('reminds an input never attempted twice, with its original event= and seq, then records it missing', async (t) => {
    const { store, activities, submitted, logs, ingest, turn } = setup(t);
    const row = ingest({ messageId: 'om_forgot1234567', chatId: 'oc_p2p12345678' });
    await turn([row.seq, null]);
    await turn([row.seq]);
    assert.deepEqual(activities.map(({ kind, status, inboundSeq, metadata }) => [kind, status, inboundSeq, metadata]),
      Array.from({ length: 2 }, () => ['agent.decision_missing', 'NUDGED', row.seq, { expectation: 'expected', runtime: 'codex' }]));
    assert.equal(submitted.length, 2);
    for (const [index, item] of submitted.entries()) {
      assert.equal(item.seq, row.seq);
      assert.equal(item.receiptId, undefined);
      assert.match(item.text, /^\[chat=oc_p2p12345678 source=lark msg=om_forgot1234567 event=message:om_forgot1234567 time=\S+ from=\S+ undecided=1\] hello$/);
      assert.equal(logs.filter((line) => line.message === 'decision_nudged')[index].reminder, index + 1);
    }
    assert.doesNotMatch(JSON.stringify(logs), /oc_p2p|om_forgot/);

    // The second reminder turn checks the same seq again but adds no third.
    await turn([row.seq]);
    assert.equal(submitted.length, 2);
    assert.deepEqual(activities.map(({ status }) => status), ['NUDGED', 'NUDGED', 'MISSING']);
    assert.equal(store.decisionOutcome(row.seq), 'missing');
    await turn([]);
    assert.equal(activities.length, 3);
  });

  it('honors a zero reminder limit', async (t) => {
    const { store, activities, submitted, ingest, turn } = setup(t, { decisionReminderLimit: 0 });
    const row = ingest({ messageId: 'om_noReminder123' });
    await turn([row.seq]);
    assert.deepEqual(submitted, []);
    assert.deepEqual(activities.map(({ status }) => status), ['MISSING']);
    assert.equal(store.decisionOutcome(row.seq), 'missing');
  });

  it('asks every input of a burst for its own decision: three decisions, or one that covers the other two', async (t) => {
    const { activities, submitted, ingest, silence, deliver, settlement, turn } = setup(t);
    const group = (prefix, chatId) => ['A', 'B', 'C']
      .map((letter) => ingest({ messageId: `om_${prefix}${letter}12345678`, chatId, chatType: 'group' }));
    // One decision each.
    const each = group('each', 'oc_each12345678');
    deliver(each[0]);
    silence(each[1]);
    deliver(each[2]);
    await turn(each.map((row) => row.seq));
    assert.deepEqual([settlement().delivered, settlement().silent, settlement().nudged], [2, 1, 0]);
    // One decision on the newest, covering the other two.
    const covered = group('cover', 'oc_cover1234567');
    deliver(covered[2], covered.slice(0, 2));
    await turn(covered.map((row) => row.seq));
    assert.deepEqual([settlement().delivered, settlement().nudged], [3, 0]);
    assert.deepEqual([activities, submitted], [[], []]);

    // The newest alone no longer settles the older two: each is reminded.
    const partly = group('part', 'oc_part12345678');
    deliver(partly[2]);
    await turn(partly.map((row) => row.seq));
    assert.deepEqual(activities.map(({ status, inboundSeq }) => [status, inboundSeq]),
      [['NUDGED', partly[0].seq], ['NUDGED', partly[1].seq]]);
    assert.deepEqual(submitted.map((item) => item.seq), [partly[0].seq, partly[1].seq]);
    // Covering one of them later settles it; the other goes on being reminded.
    deliver(partly[2], [partly[0]]);
    await turn([partly[0].seq, partly[1].seq]);
    assert.deepEqual(activities.slice(2).map(({ status, inboundSeq }) => [status, inboundSeq]), [['NUDGED', partly[1].seq]]);
    await turn([partly[1].seq]);
    assert.deepEqual(activities.at(-1).status, 'MISSING');
  });

  it('keeps reminding about a newest input that only fenced a decision covering an older one', async (t) => {
    const { store, activities, ingest, settlement, turn } = setup(t);
    const older = ingest({ messageId: 'om_fencedA123456', chatId: 'oc_fenced1234567', chatType: 'group' });
    const newest = ingest({ messageId: 'om_fencedB123456', chatId: 'oc_fenced1234567', chatType: 'group' });
    const raw = (row) => store.db.prepare('SELECT * FROM connector_events WHERE seq = ?').get(row.seq);
    store.recordDecision({ row: raw(older), outcome: 'delivered', coveredBy: raw(newest).event_id });
    await turn([older.seq, newest.seq]);
    assert.deepEqual([settlement().delivered, settlement().nudged], [1, 1]);
    assert.deepEqual(activities.map(({ status, inboundSeq }) => [status, inboundSeq]), [['NUDGED', newest.seq]]);
  });

  it('reminds about an older input with no decision of its own even when a newer one arrived', async (t) => {
    const { activities, ingest, turn } = setup(t);
    const first = ingest({ messageId: 'om_firstA1234567' });
    ingest({ messageId: 'om_secondB123456' });
    await turn([first.seq]);
    assert.deepEqual(activities.map(({ status, inboundSeq }) => [status, inboundSeq]), [['NUDGED', first.seq]]);
  });

  it('gives an input whose reply the fence held a timer reminder first, then two turn-end ones, then records it missing', async (t) => {
    const { policy, activities, submitted, timers, ingest, fire, turn } = setup(t);
    const held = ingest({ messageId: 'om_heldA12345678', chatId: 'oc_held12345678', chatType: 'group' });
    const newest = ingest({ messageId: 'om_heldB12345678', chatId: 'oc_held12345678', chatType: 'group' });
    policy.onStaleDecision({ seq: held.seq });
    // A second block changes nothing; a decided or optional input starts no timer.
    policy.onStaleDecision({ seq: held.seq });
    assert.deepEqual(timers.map(({ ms }) => ms), [60_000]);
    // The turn that held it ends before the timer: no turn-end reminder yet.
    await turn([held.seq, newest.seq]);
    assert.deepEqual(activities.map(({ inboundSeq }) => inboundSeq), [newest.seq]);
    // The timer reminds, mid-turn, with the input's seq.
    assert.equal(await fire(), 1);
    assert.deepEqual(activities.slice(1).map(({ status, inboundSeq }) => [status, inboundSeq]), [['NUDGED', held.seq]]);
    assert.match(submitted.at(-1).text, /msg=om_heldA12345678 [^\]]* undecided=1\]/);
    assert.equal(submitted.at(-1).seq, held.seq);
    // Then the shared turn-end steps: two more reminders, then MISSING.
    await turn([held.seq]);
    await turn([held.seq]);
    await turn([held.seq]);
    assert.deepEqual(activities.filter(({ inboundSeq }) => inboundSeq === held.seq).map(({ status }) => status),
      ['NUDGED', 'NUDGED', 'NUDGED', 'MISSING']);
    assert.equal(submitted.filter((item) => item.seq === held.seq).length, 3);
  });

  it('still gives a held input its timer reminder when turn-end reminders are off', async (t) => {
    const { policy, activities, timers, ingest, fire, turn } = setup(t, { decisionReminderLimit: 0 });
    const held = ingest({ messageId: 'om_zeroA123456789', chatId: 'oc_zero12345678', chatType: 'group' });
    ingest({ messageId: 'om_zeroB123456789', chatId: 'oc_zero12345678', chatType: 'group' });
    policy.onStaleDecision({ seq: held.seq });
    assert.deepEqual(timers.map(({ ms }) => ms), [60_000]);
    assert.equal(await fire(), 1);
    await turn([held.seq]);
    assert.deepEqual(activities.filter(({ inboundSeq }) => inboundSeq === held.seq).map(({ status }) => status),
      ['NUDGED', 'MISSING']);
  });

  it('drops a held input decided before its timer fires', async (t) => {
    const { store, policy, activities, timers, ingest, deliver, fire, turn } = setup(t);
    const held = ingest({ messageId: 'om_decideA123456', chatId: 'oc_decide1234567', chatType: 'group' });
    const newest = ingest({ messageId: 'om_decideB123456', chatId: 'oc_decide1234567', chatType: 'group' });
    policy.onStaleDecision({ seq: held.seq });
    deliver(newest, [held]);
    assert.equal(store.decisionOutcome(held.seq), 'delivered');
    await fire();
    await turn([held.seq, newest.seq]);
    assert.deepEqual(activities, []);
    // Nothing to remind about for a decided input or a reaction.
    const reaction = ingest({ messageId: 're_held123456789', kind: 'message.reaction.created', content: '[reaction OK]' });
    policy.onStaleDecision({ seq: held.seq });
    policy.onStaleDecision({ seq: reaction.seq });
    assert.equal(timers.length, 1);
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

  it('lets a reaction wake the Agent without asking for a decision of its own', async (t) => {
    const { activities, submitted, ingest, settlement, turn } = setup(t);
    const asked = ingest({ messageId: 'om_asked12345678' });
    const reaction = ingest({ messageId: 're_ok12345678901', kind: 'message.reaction.created', content: '[reaction OK]' });
    await turn([reaction.seq]);
    assert.deepEqual([activities, submitted], [[], []]);
    assert.deepEqual([settlement().expected_unsettled, settlement().optional_unsettled, settlement().nudged], [0, 1, 0]);
    // Nor does a reaction settle the message before it.
    await turn([asked.seq, reaction.seq]);
    assert.deepEqual(activities.map(({ status, inboundSeq }) => [status, inboundSeq]), [['NUDGED', asked.seq]]);
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

    // An older input with a newer one in its chat comes back too: nothing
    // settled it.
    await stopped([looped.seq, waiting.seq, older.seq, mail.seq]);
    assert.deepEqual(submitted.map((item) => item.seq), [waiting.seq, older.seq, mail.seq]);
    assert.match(submitted[0].text, /msg=om_waiting123456 event=message:om_waiting123456 [^\]]* undecided=1\] hello$/);
    assert.deepEqual(logs.filter((line) => line.message === 'stopped_turn_settlement').at(-1),
      { message: 'stopped_turn_settlement', decided: 1, redelivered: 3, missing: 0 });
    assert.deepEqual(activities.map(({ status, inboundSeq, metadata }) => [status, inboundSeq, metadata.expectation]),
      [['NUDGED', waiting.seq, 'loop_stopped'], ['NUDGED', older.seq, 'loop_stopped'], ['NUDGED', mail.seq, 'loop_stopped']]);

    // Held by a stopped turn again: not shown a third time, its sender is told.
    await stopped([waiting.seq]);
    assert.equal(submitted.length, 3);
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

describe('inputs a /runtime switch cut off', () => {
  it('hands on every undecided one in order, untagged, and leaves the answered ones answered', async (t) => {
    const { store, policy, submitted, ingest, silence } = setup(t);
    const answered = ingest({ messageId: 'om_answered12345', chatId: 'oc_done12345678' });
    silence(answered);
    const first = ingest({ messageId: 'om_first12345678', chatId: 'oc_work12345678' });
    const second = ingest({ messageId: 'om_second1234567', chatId: 'oc_work12345678' });
    const mail = store.ingest(mailEvent({ uid: 11 })).inbound;

    const plan = policy.resumeInterrupted({ inboundSeqs: [mail.seq, second.seq, answered.seq, first.seq, second.seq],
      from: 'codex', to: 'pi' });
    // Unlike a loop stop, the older line of a scope is shown too: the new
    // session never saw it.
    assert.deepEqual(plan.items.map((item) => item.seq), [first.seq, second.seq, mail.seq]);
    assert.doesNotMatch(plan.items[0].text, /undecided=1/);
    assert.equal(submitted.length, 0, 'the switch delivers them itself');
    assert.match(plan.notice, /It sent nothing after the first of these inputs\.$/);
  });

  it('quotes what the stopped turn already sent, and not when the switch failed', async (t) => {
    const { store, policy, ingest } = setup(t);
    ingest({ messageId: 'om_before1234567', chatId: 'oc_work12345678' });
    store.appendCognitionJournal({ kind: 'outbound', chatId: 'oc_work12345678', text: 'answered the earlier one' });
    const cut = ingest({ messageId: 'om_cutoff1234567', chatId: 'oc_work12345678' });
    store.appendCognitionJournal({ kind: 'outbound', chatId: 'oc_work12345678', text: 'on it, cloning the repo' });
    store.appendCognitionJournal({ kind: 'outbound', status: 'PENDING', chatId: 'oc_work12345678', text: 'cloned' });

    const lines = policy.resumeInterrupted({ inboundSeqs: [cut.seq], from: 'pi', to: 'codex' }).notice.split('\n');
    assert.equal(lines[1], 'What it sent after the first of these inputs:');
    assert.match(lines[2], /from=me\] on it, cloning the repo$/);
    assert.match(lines[3], /from=me outcome=uncertain\] cloned$/);
    assert.equal(lines.length, 5, 'nothing sent before the first input');
    // The old runtime's own session has what it sent.
    const failed = policy.resumeInterrupted({ inboundSeqs: [cut.seq], from: 'pi', to: 'codex', failed: true });
    assert.match(failed.notice, /switch to codex failed, so you stay on pi/);
    assert.doesNotMatch(failed.notice, /cloning/);
  });
});
