import assert from 'node:assert/strict';
import { it } from 'node:test';
import { MessageInbox } from '../src/message-inbox.mjs';

function setup(t, { busy = true, activeInput = 'steer' } = {}) {
  let now = 0;
  let available = true;
  const timers = new Map();
  const notices = [];
  const inbox = new MessageInbox({
    submit: (items, options) => {
      if (!available) return { accepted: false };
      notices.push({ items, options });
      busy = true;
      return { accepted: true };
    },
    isTurnInProgress: () => busy,
    activeInput: () => activeInput,
    setTimer: (callback, delay) => {
      const timer = { callback, at: now + delay };
      timers.set(timer, timer);
      return timer;
    },
    clearTimer: (timer) => timers.delete(timer),
  });
  t.after(() => inbox.stop());
  const advance = (milliseconds) => {
    now += milliseconds;
    for (const timer of [...timers.values()]) {
      if (timer.at <= now) { timers.delete(timer); timer.callback(); }
    }
  };
  return { inbox, notices, advance, timers,
    idle: () => { busy = false; inbox.onAvailable(); },
    setAvailable: (value) => { available = value; } };
}

function item(seq, scopeKey = 'A') {
  return { seq, scopeKey, source: 'lark', chatId: `chat-${scopeKey}`, eventId: `evt-${seq}`,
    senderName: '张三', text: `secret-${seq}` };
}

it('uses a fixed busy window and notices every scope with its whole backlog', (t) => {
  const { inbox, notices, advance } = setup(t);
  inbox.route(item(1));
  advance(2000);
  inbox.route(item(2));
  advance(999);
  assert.equal(notices.length, 0);
  advance(1);
  assert.equal(notices.length, 1);
  assert.match(notices[0].items[0].text, /pending=2 scopes=1/);
  assert.doesNotMatch(notices[0].items[0].text, /secret/);
  const first = JSON.parse(notices[0].items[0].text.split('\n')[1]);
  assert.deepEqual(first, { scope_key: 'A', source: 'lark', chat: 'chat-A', pending: 2,
    first_event: 'evt-1', latest_event: 'evt-2', latest_from: '张三' });
  inbox.route(item(3, 'B'));
  advance(3000);
  // A notice is a backlog report, not a delta: the model reads it to judge how
  // far behind it is, so a scope that is still unread stays listed.
  assert.match(notices[1].items[0].text, /pending=3 scopes=2/);
  assert.match(notices[1].items[0].text, /"scope_key":"A"/);
  assert.match(notices[1].items[0].text, /"scope_key":"B"/);
  advance(3000);
  assert.equal(notices.length, 2);
});

it('removes FIFO reads immediately, with an optional scope and bounded limit', (t) => {
  const { inbox } = setup(t);
  inbox.route(item(1));
  inbox.route(item(2, 'B'));
  inbox.route(item(3));
  assert.deepEqual(inbox.read({ scopeKey: 'A', limit: 1 }).items.map((entry) => entry.seq), [1]);
  assert.equal(inbox.pending, 2);
  assert.deepEqual(inbox.read({ limit: 10 }).items.map((entry) => entry.seq), [2, 3]);
  assert.equal(inbox.read().items.length, 0);
  assert.equal(inbox.pending, 0);
});

it('does not notify messages already consumed during the notification window', (t) => {
  const { inbox, notices, advance } = setup(t);
  inbox.route(item(1));
  inbox.route(item(2, 'B'));
  inbox.read({ scopeKey: 'A' });
  advance(3000);
  assert.match(notices[0].items[0].text, /pending=1 scopes=1/);
  assert.doesNotMatch(notices[0].items[0].text, /"scope_key":"A"/);
  inbox.read();
  advance(3000);
  assert.equal(notices.length, 1);
});

it('wakes immediately when idle and stays quiet while nothing new arrives', (t) => {
  const { inbox, notices, idle } = setup(t, { busy: false });
  inbox.route(item(1));
  assert.equal(notices.length, 1);
  assert.equal(notices[0].options.admission, 'claim-if-idle');
  // A turn that ends without reading must not inject the same notice again:
  // that turn ends unread, calls onAvailable, and the pair loops forever.
  idle();
  assert.equal(notices.length, 1);
  inbox.route(item(2));
  idle();
  assert.equal(notices.length, 2);
  assert.match(notices[1].items[0].text, /pending=2 scopes=1/);
  inbox.read();
  idle();
  assert.equal(notices.length, 2);
});

it('defers notices on queue-only runtimes until idle', (t) => {
  const { inbox, notices, advance, idle } = setup(t, { activeInput: 'queue-next-turn' });
  inbox.route(item(1));
  advance(3000);
  assert.equal(notices.length, 0);
  idle();
  assert.equal(notices.length, 1);
  assert.match(notices[0].items[0].text, /pending=1/);
});

it('retains unnotified items when admission refuses and retries on availability', (t) => {
  const { inbox, notices, advance, idle, setAvailable } = setup(t);
  setAvailable(false);
  inbox.route(item(1));
  advance(3000);
  assert.equal(notices.length, 0);
  assert.equal(inbox.pending, 1);
  setAvailable(true);
  idle();
  assert.equal(notices.length, 1);
});

it('drops unread items and cancels notification timers on stop without redelivery', (t) => {
  const { inbox, notices, advance, timers, idle } = setup(t);
  inbox.route(item(1));
  inbox.stop();
  assert.equal(timers.size, 0);
  assert.equal(inbox.pending, 0);
  advance(3000);
  idle();
  assert.equal(inbox.route(item(2)), false);
  assert.equal(notices.length, 0);
});

it('does not lose messages when recording a read fails', (t) => {
  const { inbox } = setup(t);
  inbox.route(item(1));
  inbox.onRead = () => { throw new Error('record failed'); };
  assert.throws(() => inbox.read(), /record failed/);
  assert.equal(inbox.pending, 1);
  inbox.onRead = () => {};
  assert.deepEqual(inbox.read().items.map((entry) => entry.seq), [1]);
});
