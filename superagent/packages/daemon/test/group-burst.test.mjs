// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  GROUP_BURST_DELAY_MS,
  GroupBurstRouter,
} from '../src/group-burst.mjs';

function fakeClock() {
  let currentTime = 0;
  let nextId = 0;
  const timers = new Map();

  return {
    now: () => currentTime,
    setTimer(callback, delayMs) {
      const handle = { id: ++nextId, unref() {} };
      timers.set(handle, {
        atMs: currentTime + delayMs,
        callback,
        order: handle.id,
      });
      return handle;
    },
    clearTimer(handle) {
      timers.delete(handle);
    },
    advance(delayMs) {
      const targetTime = currentTime + delayMs;
      while (true) {
        const next = [...timers.entries()]
          .filter(([, timer]) => timer.atMs <= targetTime)
          .sort(([, left], [, right]) => (
            left.atMs - right.atMs || left.order - right.order
          ))[0];
        if (!next) break;
        const [handle, timer] = next;
        timers.delete(handle);
        currentTime = timer.atMs;
        timer.callback();
      }
      currentTime = targetTime;
    },
  };
}

function message({
  conversationKey = 'test:tenant:oc_group:main',
  chatType = 'group',
  seq,
  text = `message ${seq}`,
}) {
  return {
    conversationKey,
    chatType,
    seq,
    text,
  };
}

function newRouter({
  clock = fakeClock(),
  deliveries = [],
  connected = () => true,
  busy = () => false,
  deliverBatch = (items) => deliveries.push(items),
  tryStartBatch = (items) => (
    connected() && !busy() && deliverBatch(items) !== false
  ),
} = {}) {
  const router = new GroupBurstRouter({
    submit: (items, { admission = 'enqueue' } = {}) => ({
      accepted: (admission === 'claim-if-idle' ? tryStartBatch(items) : deliverBatch(items)) !== false,
    }),
    isConnected: connected,
    isTurnInProgress: busy,
    now: clock.now,
    setTimer: clock.setTimer,
    clearTimer: clock.clearTimer,
  });
  return { router, clock, deliveries };
}

describe('warm group burst routing', () => {
  it('uses one fixed 400 ms deadline for a same-conversation correction', () => {
    assert.equal(GROUP_BURST_DELAY_MS, 400);
    const { router, clock, deliveries } = newRouter();

    router.route(message({ seq: 1, text: 'draft request' }));
    clock.advance(399);
    router.route(message({ seq: 2, text: 'correction' }));
    assert.equal(deliveries.length, 0);

    clock.advance(1);
    assert.deepEqual(
      deliveries.map((batch) => batch.map((item) => item.seq)),
      [[1, 2]],
    );
    assert.equal(router.pendingConversations, 0);
  });

  it('does not merge a post-deadline message when the timer callback runs late', () => {
    let currentTime = 0;
    const deliveries = [];
    const router = new GroupBurstRouter({
      submit(items) {
        deliveries.push(items);
        return { accepted: true };
      },
      isConnected: () => true,
      isTurnInProgress: () => false,
      now: () => currentTime,
      setTimer: () => ({ unref() {} }),
      clearTimer() {},
    });

    router.route(message({ seq: 1 }));
    currentTime = 500;
    router.route(message({ seq: 2 }));

    assert.deepEqual(deliveries.map((batch) => batch.map((item) => item.seq)), [[1]]);
    assert.deepEqual(
      router.pending.get('test:tenant:oc_group:main').items.map((item) => item.seq),
      [2],
    );
  });

  it('keeps different chats and threads in separate turns', () => {
    const clock = fakeClock();
    const deliveries = [];
    let busy = false;
    const { router } = newRouter({
      clock,
      deliveries,
      busy: () => busy,
      deliverBatch(items) {
        deliveries.push(items);
        busy = true;
      },
    });

    router.route(message({
      conversationKey: 'test:tenant:oc_shared:main',
      seq: 1,
    }));
    clock.advance(100);
    router.route(message({
      conversationKey: 'test:tenant:oc_shared:thread:omt_topic',
      seq: 2,
    }));
    clock.advance(300);

    assert.deepEqual(deliveries.map((batch) => batch.map((item) => item.seq)), [[1]]);
    assert.equal(router.pendingConversations, 1);

    busy = false;
    router.onAvailable();
    assert.deepEqual(deliveries.map((batch) => batch.map((item) => item.seq)), [[1]]);
    clock.advance(100);
    assert.deepEqual(
      deliveries.map((batch) => batch.map((item) => item.seq)),
      [[1], [2]],
    );
  });

  it('does not steer an expired conversation into an older ready conversation', () => {
    const clock = fakeClock();
    const deliveries = [];
    let busy = false;
    const { router } = newRouter({
      clock,
      deliveries,
      busy: () => busy,
      deliverBatch(items) {
        deliveries.push(items);
        busy = true;
      },
    });
    const conversationA = 'test:tenant:oc_a:main';
    const conversationB = 'test:tenant:oc_b:main';

    router.route(message({ conversationKey: conversationA, seq: 1 }));
    clock.advance(50);
    router.route(message({ conversationKey: conversationB, seq: 2 }));
    // Simulate an event-loop stall past both fixed deadlines.
    clock.clearTimer(router.timer);
    router.timer = null;
    clock.advance(450);
    router.route(message({ conversationKey: conversationB, seq: 3 }));

    assert.deepEqual(deliveries.map((batch) => batch.map((item) => item.seq)), [[1]]);
    assert.deepEqual(
      router.pending.get(conversationB).items.map((item) => item.seq),
      [2, 3],
    );

    busy = false;
    router.onAvailable();
    assert.deepEqual(
      deliveries.map((batch) => batch.map((item) => item.seq)),
      [[1], [2, 3]],
    );
  });

  it('routes P2P and live-turn group messages immediately', () => {
    const clock = fakeClock();
    const deliveries = [];
    let busy = false;
    const { router } = newRouter({
      clock,
      deliveries,
      busy: () => busy,
    });

    router.route(message({
      conversationKey: 'test:tenant:oc_p2p:main',
      chatType: 'p2p',
      seq: 1,
    }));
    assert.deepEqual(deliveries.map((batch) => batch.map((item) => item.seq)), [[1]]);

    router.route(message({ seq: 2, text: 'first group line' }));
    clock.advance(100);
    busy = true;
    router.route(message({ seq: 3, text: 'live correction' }));
    assert.deepEqual(
      deliveries.map((batch) => batch.map((item) => item.seq)),
      [[1], [2, 3]],
    );
    assert.equal(router.pendingConversations, 0);
  });

  it('does not hold a group message while the global Codex thread is cold', () => {
    const deliveries = [];
    const { router } = newRouter({
      deliveries,
      connected: () => false,
    });

    router.route(message({ seq: 1 }));
    assert.deepEqual(deliveries.map((batch) => batch.map((item) => item.seq)), [[1]]);
  });

  it('keeps a due group pending while P2P owns the turn, then serves it', () => {
    const clock = fakeClock();
    const deliveries = [];
    let busy = false;
    const { router } = newRouter({
      clock,
      deliveries,
      busy: () => busy,
      deliverBatch(items) {
        deliveries.push(items);
        busy = true;
      },
    });

    router.route(message({ seq: 1 }));
    clock.advance(100);
    router.route(message({
      conversationKey: 'test:tenant:oc_p2p:main',
      chatType: 'p2p',
      seq: 2,
    }));
    clock.advance(300);

    assert.deepEqual(deliveries.map((batch) => batch.map((item) => item.seq)), [[2]]);
    assert.equal(router.pendingConversations, 1);

    busy = false;
    router.onAvailable();
    assert.deepEqual(
      deliveries.map((batch) => batch.map((item) => item.seq)),
      [[2], [1]],
    );
  });

  it('retains ownership when Codex is busy or immediate delivery throws', () => {
    const clock = fakeClock();
    let canStart = false;
    let active = false;
    let throwDelivery = false;
    const deliveries = [];
    const { router } = newRouter({
      clock,
      deliveries,
      busy: () => active,
      deliverBatch(items) {
        if (throwDelivery) throw new Error('delivery failed');
        deliveries.push(items);
        return true;
      },
      tryStartBatch(items) {
        if (!canStart) return false;
        deliveries.push(items);
        return true;
      },
    });

    router.route(message({ seq: 1 }));
    clock.advance(400);
    assert.equal(router.pendingConversations, 1);
    assert.equal(deliveries.length, 0);

    canStart = true;
    router.onAvailable();
    assert.deepEqual(deliveries.map((batch) => batch.map((item) => item.seq)), [[1]]);

    router.route(message({ seq: 2 }));
    active = true;
    throwDelivery = true;
    assert.throws(
      () => router.route(message({ seq: 3 })),
      /delivery failed/,
    );
    assert.equal(router.pendingConversations, 1);
    assert.deepEqual(
      router.pending.get('test:tenant:oc_group:main').items.map((item) => item.seq),
      [2, 3],
    );
  });

  it('retains an immediate input when a runtime transition closes admission', () => {
    const clock = fakeClock();
    const deliveries = [];
    let transitioning = true;
    const router = new GroupBurstRouter({
      submit(items) {
        if (transitioning) return { accepted: false, reason: 'switching' };
        deliveries.push(items);
        return { accepted: true, disposition: 'enqueued' };
      },
      isConnected: () => true,
      isTurnInProgress: () => true,
      now: clock.now,
      setTimer: clock.setTimer,
      clearTimer: clock.clearTimer,
    });

    assert.equal(router.route(message({
      conversationKey: 'test:tenant:oc_p2p:main',
      chatType: 'p2p',
      seq: 9,
    })), true);
    assert.equal(router.pendingConversations, 1);
    assert.equal(deliveries.length, 0);

    transitioning = false;
    router.onAvailable();
    assert.deepEqual(deliveries.map((batch) => batch.map((item) => item.seq)), [[9]]);
    assert.equal(router.pendingConversations, 0);
  });
});
