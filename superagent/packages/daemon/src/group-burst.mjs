// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

export const GROUP_BURST_DELAY_MS = 400;

// Transport-only batching for warm, idle group traffic. Conversation identity is
// used only to keep a short inbound burst together; Codex still owns one global
// cognitive thread and keeps its existing live turn/steer behavior.
export class GroupBurstRouter {
  constructor({
    submit,
    isConnected,
    isTurnInProgress,
    delayMs = GROUP_BURST_DELAY_MS,
    now = Date.now,
    setTimer = setTimeout,
    clearTimer = clearTimeout,
  }) {
    if (typeof submit !== 'function') throw new TypeError('submit must be a function');
    if (typeof isConnected !== 'function') throw new TypeError('isConnected must be a function');
    if (typeof isTurnInProgress !== 'function') {
      throw new TypeError('isTurnInProgress must be a function');
    }
    if (!Number.isFinite(delayMs) || delayMs < 0) {
      throw new TypeError('delayMs must be a non-negative number');
    }

    this.submit = submit;
    this.isConnected = isConnected;
    this.isTurnInProgress = isTurnInProgress;
    this.delayMs = delayMs;
    this.now = now;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.pending = new Map();
    this.nextOrder = 0;
    this.timer = null;
    this.stopped = false;
  }

  get pendingConversations() {
    return this.pending.size;
  }

  route(item) {
    if (this.stopped) return false;
    const key = item?.scopeKey ?? item?.conversationKey;
    if (typeof key !== 'string' || !key) {
      throw new TypeError('event scopeKey is required');
    }

    let existing = this.pending.get(key);
    if (item.chatType === 'group'
      && existing
      && existing.dueAtMs <= this.now()
      && this.isConnected()
      && !this.isTurnInProgress()) {
      const claimed = this.flushReady();
      existing = this.pending.get(key);
      // An older conversation may have won the idle turn. Keep this expired
      // conversation sealed behind it instead of steering across chat/thread.
      if (claimed && existing) {
        existing.items.push(item);
        return true;
      }
    }
    const immediate = item.chatType !== 'group'
      || !this.isConnected()
      || this.isTurnInProgress();

    if (immediate) {
      if (existing) {
        existing.items.push(item);
      } else {
        existing = {
          scopeKey: key,
          dueAtMs: this.now(),
          order: this.nextOrder++,
          items: [item],
        };
        // Retain ownership before calling into the runtime. A transition may
        // close admission between the status check and submit.
        this.pending.set(key, existing);
      }
      const accepted = this.submit(existing.items, {
        admission: 'enqueue',
      })?.accepted === true;
      if (accepted) this.pending.delete(key);
      this._schedule();
      return true;
    }

    if (existing) {
      existing.items.push(item);
    } else {
      this.pending.set(key, {
        scopeKey: key,
        dueAtMs: this.now() + this.delayMs,
        order: this.nextOrder++,
        items: [item],
      });
    }
    this._schedule();
    return true;
  }

  onAvailable() {
    this.flushReady();
  }

  flushReady() {
    if (this.stopped) return false;
    const currentTime = this.now();
    const next = [...this.pending.values()]
      .filter((entry) => entry.dueAtMs <= currentTime)
      .sort((left, right) => left.dueAtMs - right.dueAtMs || left.order - right.order)[0];
    if (!next) {
      this._schedule();
      return false;
    }

    if (this.submit(next.items, { admission: 'claim-if-idle' })?.accepted !== true) return false;
    this.pending.delete(next.scopeKey);
    return true;
  }

  // Global slash-command barrier: hand every already-arrived group batch to
  // the current runtime before a /runtime or /new control action can overtake it.
  flushAll() {
    if (this.stopped || this.pending.size === 0) return 0;
    const entries = [...this.pending.values()]
      .sort((left, right) => left.order - right.order);
    let flushed = 0;
    for (const entry of entries) {
      if (this.submit(entry.items, { admission: 'enqueue' })?.accepted !== true) break;
      this.pending.delete(entry.scopeKey);
      flushed += 1;
    }
    this._schedule();
    return flushed;
  }

  _schedule() {
    if (this.timer) this.clearTimer(this.timer);
    this.timer = null;
    if (this.stopped || this.pending.size === 0) return;

    const dueAtMs = Math.min(...[...this.pending.values()].map((entry) => entry.dueAtMs));
    this.timer = this.setTimer(() => {
      this.timer = null;
      this.flushReady();
    }, Math.max(0, dueAtMs - this.now()));
    this.timer?.unref?.();
  }

  stop() {
    this.stopped = true;
    if (this.timer) this.clearTimer(this.timer);
    this.timer = null;
    this.pending.clear();
  }
}
