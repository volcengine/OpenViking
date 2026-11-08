// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { MessageInbox, inboxNoticeText, NOTICE_DELAY_MS, WAKE_DELAY_MS } from '../src/inbox.mjs';

function fakeRuntime() {
  return {
    busy: false,
    accept: true,
    submissions: [],
    snapshot() { return { session: { connected: true }, work: { turnInProgress: this.busy } }; },
    submit(items, { admission }) {
      if (!this.accept) return { accepted: false, reason: 'switching' };
      if (admission === 'claim-if-idle' && this.busy) return { accepted: false, reason: 'busy' };
      this.submissions.push({ admission, items });
      if (admission === 'claim-if-idle') this.busy = true;
      return { accepted: true };
    },
  };
}

function fakeClock() {
  let now = 1_000_000;
  const timers = new Set();
  return {
    now: () => now,
    setTimer(fn, ms) { const timer = { at: now + ms, fn }; timers.add(timer); return timer; },
    clearTimer(timer) { timers.delete(timer); },
    advance(ms) {
      now += ms;
      for (const timer of [...timers].sort((a, b) => a.at - b.at)) {
        if (timer.at <= now && timers.has(timer)) { timers.delete(timer); timer.fn(); }
      }
    },
  };
}

const CORE_STEER = { agentTools: 'core', activeInput: 'steer' };

function setup({ capabilities = CORE_STEER, db = new DatabaseSync(':memory:'), runtime = fakeRuntime(), clock = fakeClock(), maxAgeMs } = {}) {
  const activities = [];
  const inbox = new MessageInbox({
    db, runtime, capabilities: () => capabilities, now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer,
    recordActivity: (kind, fields) => activities.push({ kind, ...fields }),
    ...(maxAgeMs ? { maxAgeMs } : {}),
  });
  return { inbox, runtime, clock, db, activities };
}

let nextSeq = 0;
function input(chat, { chatType = 'group', thread = null, from = 'Alice', owner = false, mention = null, text } = {}) {
  const seq = ++nextSeq;
  return [
    { seq, text: text ?? `[chat=${chat} msg=om_${seq} event=e${seq} from=${from}] secret body ${seq}`,
      scopeKey: `lark:${chat}:${thread ?? ''}`, chatType },
    { chatId: chat, threadId: thread, source: 'lark', instance: 'lark', kind: 'message.created', chatType, from, owner, mention },
  ];
}

const texts = (submission) => submission.items.map((item) => item.text);

describe('MessageInbox while the runtime is idle', () => {
  it('starts a turn at once for a direct line and after the burst window for a group line', () => {
    const { inbox, runtime, clock } = setup();
    inbox.add(...input('oc_dm', { chatType: 'p2p' }));
    clock.advance(0);
    assert.equal(runtime.submissions.length, 1);
    assert.equal(runtime.submissions[0].admission, 'claim-if-idle');
    runtime.busy = false;

    inbox.add(...input('oc_group'));
    clock.advance(WAKE_DELAY_MS - 1);
    inbox.add(...input('oc_group'));
    assert.equal(runtime.submissions.length, 1);
    clock.advance(1);
    assert.equal(runtime.submissions.length, 2);
    assert.equal(runtime.submissions[1].items.length, 2, 'the burst starts one turn');
    assert.equal(inbox.size, 0);
  });

  it('starts with the oldest waiting inputs and notices the rest in that turn', () => {
    const { runtime, clock, db } = setup();
    const inbox = new MessageInbox({ db, runtime, capabilities: () => CORE_STEER, wakeBatch: 2,
      now: clock.now, setTimer: clock.setTimer, clearTimer: clock.clearTimer });
    runtime.busy = true;
    const lines = [input('oc_a'), input('oc_b'), input('oc_a')];
    for (const line of lines) inbox.add(...line);
    runtime.busy = false;
    inbox.onAvailable();
    clock.advance(WAKE_DELAY_MS);
    const [turn] = runtime.submissions.filter((submission) => submission.admission === 'claim-if-idle');
    assert.deepEqual(turn.items.slice(0, 2).map((item) => item.seq), [lines[0][0].seq, lines[1][0].seq]);
    assert.match(turn.items[2].text, /^\[daemon inbox\] waiting=1 chats=1/);
    assert.equal(inbox.size, 1);
  });

  it('retries a wake the runtime refused, and leaves inputs waiting meanwhile', () => {
    const { inbox, runtime, clock } = setup();
    runtime.accept = false;
    inbox.add(...input('oc_dm', { chatType: 'p2p' }));
    clock.advance(0);
    assert.equal(runtime.submissions.length, 0);
    assert.equal(inbox.size, 1);
    runtime.accept = true;
    clock.advance(1_000);
    assert.equal(runtime.submissions.length, 1);
    assert.equal(inbox.size, 0);
  });
});

describe('MessageInbox while the model works', () => {
  it('sends one notice per burst, without message text, not extended by later inputs', () => {
    const { inbox, runtime, clock, activities } = setup();
    runtime.busy = true;
    inbox.add(...input('oc_a', { from: 'Alice' }));
    clock.advance(NOTICE_DELAY_MS - 1_000);
    inbox.add(...input('oc_b', { from: 'Bob', owner: true, mention: 'me' }));
    clock.advance(1_000);
    assert.equal(runtime.submissions.length, 1);
    const [notice] = runtime.submissions;
    assert.equal(notice.admission, 'enqueue');
    assert.equal(notice.items.length, 1);
    assert.equal(notice.items[0].seq, null);
    const [text] = texts(notice);
    assert.match(text, /^\[daemon inbox\] waiting=2 chats=2; new since the last notice:/);
    assert.match(text, /- chat=oc_a source=lark type=group waiting=1 from=Alice\n/);
    assert.match(text, /- chat=oc_b source=lark type=group waiting=1 from=Bob owner=1 mention=me\n/);
    assert.match(text, /needs no decision/);
    assert.doesNotMatch(text, /secret body/);
    assert.equal(inbox.size, 2, 'a notice delivers nothing');
    assert.equal(activities.at(-1).kind, 'inbox.notified');
  });

  it('a later notice names only the chats with new inputs and counts everything waiting', () => {
    const { inbox, runtime, clock } = setup();
    runtime.busy = true;
    inbox.add(...input('oc_a'));
    clock.advance(NOTICE_DELAY_MS);
    inbox.add(...input('oc_b'));
    clock.advance(NOTICE_DELAY_MS);
    const [, second] = runtime.submissions.map(texts).map(([text]) => text);
    assert.match(second, /^\[daemon inbox\] waiting=2 chats=2/);
    assert.match(second, /chat=oc_b/);
    assert.doesNotMatch(second, /chat=oc_a/);
    clock.advance(NOTICE_DELAY_MS * 3);
    assert.equal(runtime.submissions.length, 2, 'no repeat without new inputs');
  });

  it('message_check delivers the oldest first, optionally from one chat or thread, and removes them', () => {
    const { inbox, runtime } = setup();
    runtime.busy = true;
    const a1 = input('oc_a');
    const b1 = input('oc_b', { thread: 'omt_1' });
    const a2 = input('oc_a');
    const b2 = input('oc_b', { thread: 'omt_2' });
    for (const line of [a1, b1, a2, b2]) inbox.add(...line);

    const one = inbox.check({ chatId: 'oc_b', threadId: 'omt_2' });
    assert.deepEqual(one, { delivered: 1, waiting: 3, now: true });
    assert.deepEqual(runtime.submissions.at(-1).items.map((item) => item.seq), [b2[0].seq]);

    const rest = inbox.check({ limit: 2 });
    assert.equal(rest.delivered, 2);
    assert.deepEqual(runtime.submissions.at(-1).items.map((item) => item.seq), [a1[0].seq, b1[0].seq]);
    assert.equal(runtime.submissions.at(-1).admission, 'enqueue');
    assert.deepEqual(inbox.check({ chatId: 'oc_x' }), { delivered: 0, waiting: 1 });
  });

  it('a held delivery delivers only the waiting inputs of its own scope', () => {
    const { inbox, runtime } = setup();
    runtime.busy = true;
    const a = input('oc_a');
    inbox.add(...a);
    inbox.add(...input('oc_b'));
    assert.equal(inbox.hasWaiting(a[0].scopeKey), true);
    assert.equal(inbox.deliverScope(a[0].scopeKey), 1);
    assert.equal(inbox.deliverScope(a[0].scopeKey), 0);
    assert.equal(inbox.size, 1);
  });

  it('when the turn ends, what is still waiting starts the next turn', () => {
    const { inbox, runtime, clock } = setup();
    runtime.busy = true;
    inbox.add(...input('oc_a'));
    runtime.busy = false;
    inbox.onAvailable();
    clock.advance(WAKE_DELAY_MS);
    assert.equal(runtime.submissions.at(-1).admission, 'claim-if-idle');
    assert.equal(inbox.size, 0);
  });

  it('a runtime that queues mid-turn input is not notified; it gets the inputs when the turn ends', () => {
    const { inbox, runtime, clock } = setup({ capabilities: { agentTools: 'core', activeInput: 'queue-next-turn' } });
    runtime.busy = true;
    inbox.add(...input('oc_a'));
    clock.advance(NOTICE_DELAY_MS * 2);
    assert.equal(runtime.submissions.length, 0);
    assert.equal(inbox.canNotify(), false);
    runtime.busy = false;
    inbox.onAvailable();
    clock.advance(WAKE_DELAY_MS);
    assert.equal(runtime.submissions.length, 1);
  });
});

describe('MessageInbox durability and fallback', () => {
  it('publishes a producer entry only after its transaction commits', () => {
    const { db, inbox, runtime, clock } = setup();
    db.exec('BEGIN IMMEDIATE');
    inbox.persist(...input('oc_a'));
    db.exec('ROLLBACK');
    clock.advance(WAKE_DELAY_MS);
    assert.equal(inbox.size, 0);
    assert.equal(runtime.submissions.length, 0);
    assert.equal(db.prepare('SELECT COUNT(*) n FROM inbox_pending').get().n, 0);
    db.exec('BEGIN IMMEDIATE');
    const entry = inbox.persist(...input('oc_b'));
    db.exec('COMMIT');
    inbox.publish(entry);
    inbox.publish(entry);
    clock.advance(WAKE_DELAY_MS);
    assert.equal(runtime.submissions.length, 1);
    assert.equal(runtime.submissions[0].items.length, 1);
  });

  it('retains task reports across outages longer than the event age limit', () => {
    const { db, inbox, runtime, clock } = setup();
    runtime.busy = true;
    inbox.add(...input('oc_a'));
    inbox.add({ text: 'complete task result', scopeKey: 'task:t1' }, { kind: 'task.report' });
    inbox.stop();
    clock.advance(20_000);
    const second = setup({ db, clock, maxAgeMs: 10_000 });
    assert.equal(second.inbox.restore(), 1);
    clock.advance(0);
    assert.deepEqual(texts(second.runtime.submissions[0]), ['complete task result']);
  });

  it('keeps waiting inputs across a restart and drops ones older than the event age limit', () => {
    const db = new DatabaseSync(':memory:');
    const clock = fakeClock();
    const first = setup({ db, clock });
    first.runtime.busy = true;
    const old = input('oc_a');
    first.inbox.add(...old);
    clock.advance(10_000);
    const recent = input('oc_b');
    first.inbox.add(...recent);
    first.inbox.stop();

    clock.advance(5_000);
    const second = setup({ db, clock, maxAgeMs: 12_000 });
    assert.equal(second.inbox.restore(), 1);
    clock.advance(WAKE_DELAY_MS);
    assert.deepEqual(second.runtime.submissions[0].items.map((item) => item.seq), [recent[0].seq]);
    assert.equal(db.prepare('SELECT COUNT(*) AS n FROM inbox_pending').get().n, 0);
  });

  it('is not usable on a runtime without Core tools, and drains everything in order there', () => {
    const { inbox, runtime } = setup({ capabilities: { agentTools: 'lark-cli', activeInput: 'queue-next-turn' } });
    assert.equal(inbox.usable(), false);
    runtime.busy = true;
    const lines = [input('oc_a'), input('oc_b')];
    for (const line of lines) inbox.add(...line);
    assert.equal(inbox.drain(), 2);
    assert.deepEqual(runtime.submissions.at(-1).items.map((item) => item.seq), lines.map(([item]) => item.seq));
    assert.equal(runtime.submissions.at(-1).admission, 'enqueue');
  });

  it('describes a non-chat input by its source and kind', () => {
    const entry = { scopeKey: 'mail', meta: { source: 'email', instance: 'email-work', kind: 'email.received', from: 'Carol' } };
    assert.match(inboxNoticeText([entry], [entry]), /- source=email instance=email-work kind=email\.received waiting=1 from=Carol/);
  });
});
