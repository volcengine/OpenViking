// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// When a chat's staged lines reach the Core. The Core wakes the model for
// every event and refuses a reply once a newer event arrived in that chat, so
// a busy group delivered line by line would keep every answer stale. Lines
// addressed to the agent go at once; unaddressed chatter waits until the chat
// has been quiet for a moment; context only rides along. This is scheduling
// alone: the lines themselves stay in the Connector's state.

export const MAX_HOLD_MS = 45_000;
export const TURN_GRACE_MS = 20_000;
export const CONTEXT_LINES = 30;
export const CONTEXT_AGE_MS = 6 * 60 * 60 * 1000;

export class ReleaseGate {
  // `settleMs()` is read on every decision, so a policy reload applies to
  // lines already waiting. `release(chat)` must deliver every line staged in
  // that chat; the gate counts the chat as released when it calls it. The
  // clock is monotonic: a wall clock stepped back would re-arm a waiting chat
  // for as long as it stepped, past MAX_HOLD.
  constructor({ settleMs, release, now = () => performance.now(), setTimer = setTimeout, clearTimer = clearTimeout }) {
    this.settleMs = settleMs;
    this.release = release;
    this.now = now;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.chats = new Map();
    this.timer = null;
    this.timerAt = null;
    this.releasing = false;
  }

  chat(id) {
    let state = this.chats.get(id);
    if (!state) {
      state = { lastActivity: -Infinity, lastRelease: null, grace: false, firstAmbient: null, immediate: false };
      this.chats.set(id, state);
    }
    return state;
  }

  // A line of class `cls` was staged in `chat` at `at` (the gate's clock).
  staged(chat, cls, at = this.now()) {
    const state = this.chat(chat);
    state.lastActivity = Math.max(state.lastActivity, at);
    if (cls === 'direct') state.immediate = true;
    else if (cls === 'ambient') state.firstAmbient ??= at;
    this.run();
  }

  // The Connector sent to `chat`; `at` is when the send was written. A send
  // written after the chat's last release answers the batch that release
  // gave, so its grace ends; one written before answered an earlier batch and
  // leaves it. Either way the send is activity the chat must then be quiet after.
  sent(chat, at = this.now()) {
    const state = this.chat(chat);
    state.lastActivity = Math.max(state.lastActivity, this.now());
    if (state.lastRelease === null || at >= state.lastRelease) state.grace = false;
    this.run();
  }

  // After a restart: lines that were waiting have waited long enough.
  resume(chat, classes) {
    if (!classes.has('direct') && !classes.has('ambient')) return;
    this.chat(chat).immediate = true;
    this.run();
  }

  stop() {
    this.chats.clear();
    this.cancel();
  }

  due(state) {
    if (state.immediate) return -Infinity;
    if (state.firstAmbient === null) return null;
    const grace = state.grace ? state.lastRelease + TURN_GRACE_MS : -Infinity;
    return Math.min(Math.max(state.lastActivity + this.settleMs(), grace), state.firstAmbient + MAX_HOLD_MS);
  }

  // Releases every chat that is due, earliest first, then arms one timer for
  // the next. A release that stages more lines (or stops the gate) is picked
  // up by this same loop rather than re-entering it.
  run() {
    if (this.releasing) return;
    this.releasing = true;
    try {
      for (;;) {
        const now = this.now();
        let next = null;
        for (const [chat, state] of this.chats) {
          const due = this.due(state);
          if (due !== null && due <= now && (!next || due < next.due)) next = { chat, state, due };
        }
        if (!next) break;
        Object.assign(next.state, { immediate: false, firstAmbient: null, lastRelease: now, grace: true });
        this.release(next.chat);
      }
    } finally {
      this.releasing = false;
      this.schedule();
    }
  }

  schedule() {
    let next = null;
    for (const state of this.chats.values()) {
      const due = this.due(state);
      if (due !== null && (next === null || due < next)) next = due;
    }
    if (next === this.timerAt) return;
    this.cancel();
    if (next === null) return;
    this.timerAt = next;
    this.timer = this.setTimer(() => {
      this.timer = null;
      this.timerAt = null;
      this.run();
    }, Math.max(0, next - this.now()));
  }

  cancel() {
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
    this.timerAt = null;
  }
}
