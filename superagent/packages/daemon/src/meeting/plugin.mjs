// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { MeetingAdapter } from './adapter.mjs';
import { MeetingInboxNotifier } from './inbox.mjs';

// Meeting is a standalone inbound plugin: VC events stay in the meeting store,
// and the inbox notifier tells the runtime when meeting messages are waiting.
export class MeetingPlugin {
  constructor({
    store,
    lark,
    logger,
  }) {
    this.store = store;
    this.lark = lark;
    this.logger = logger;
    this.adapter = null;
    this.inbox = null;
  }

  async prepare() {
    this.adapter = new MeetingAdapter({
      store: this.store.meetings,
      lark: this.lark,
      logger: this.logger,
      onInboxReady: (batch) => this.inbox?.signal(batch),
    });
    return this;
  }

  attachHarness({ runtime }) {
    this.inbox = new MeetingInboxNotifier({
      store: this.store.meetings,
      runtime,
      logger: this.logger.child({ component: 'meeting-inbox' }),
    });
    return this;
  }

  start() {
    this.inbox?.start();
    return this.adapter?.start() ?? Promise.resolve();
  }

  handleEvent(input) {
    return this.adapter.handleEvent(input);
  }

  async stop() {
    this.inbox?.stop();
    try { await this.adapter?.stop(); } catch {}
  }
}
