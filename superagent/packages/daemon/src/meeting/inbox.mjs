// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

export { readMeetingInboxDatabase } from './store.mjs';

export class MeetingInboxNotifier {
  constructor({
    store,
    runtime,
    logger,
    debounceMs = 50,
    retryMs = 1_000,
    setTimer = setTimeout,
    clearTimer = clearTimeout,
  }) {
    Object.assign(this, {
      store,
      runtime,
      logger,
      debounceMs,
      retryMs,
      setTimer,
      clearTimer,
    });
    this.batchIds = new Set();
    this.timer = null;
    this.stopped = false;
  }

  start() {
    if (this.store.unreadMeetingInboxCount() > 0) this.signal();
  }

  signal(batch = null) {
    if (this.stopped) return false;
    if (batch?.batchId != null) this.batchIds.add(batch.batchId);
    if (this.timer) return true;
    this.timer = this.setTimer(() => {
      this.timer = null;
      this.flush();
    }, this.debounceMs);
    this.timer?.unref?.();
    return true;
  }

  flush() {
    if (this.stopped) return false;
    const pending = this.store.unreadMeetingInboxCount();
    if (!pending) {
      this.batchIds.clear();
      return false;
    }
    const ids = [...this.batchIds];
    const admission = this.runtime.submit([{
      text: `${pending} pending meeting messages; use read_meeting_inbox to read.`,
      seq: null,
      receiptId: null,
      scope: { kind: 'meeting-inbox' },
    }]);
    if (admission.accepted !== true) {
      this.timer = this.setTimer(() => {
        this.timer = null;
        this.flush();
      }, this.retryMs);
      this.timer?.unref?.();
      return false;
    }
    if (ids.length) this.store.markMeetingInboxBatchesNotified(ids);
    this.batchIds.clear();
    this.logger?.info?.('meeting_inbox_runtime_notified', {
      pending,
      generations: Math.max(1, ids.length),
      disposition: admission.disposition,
    });
    return true;
  }

  stop() {
    this.stopped = true;
    if (this.timer) this.clearTimer(this.timer);
    this.timer = null;
    this.batchIds.clear();
  }
}
