// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The scheduler has no provider dependencies. DingTalk owns staging and
// verifies admission again when handing a batch to the Core.
import { CONTEXT_AGE_MS, CONTEXT_LINES, ReleaseGate } from '../onebot/gate.mjs';

export class DingTalkGate {
  constructor(state, deliver, onError) {
    this.state = state;
    this.onError = onError;
    this.clock = new ReleaseGate({ settleMs: () => 3_000, release: (group) => {
      try {
        this.prune(group);
        deliver(group);
      } catch (error) {
        onError(error);
      }
    } });
  }

  prune(group) {
    this.state.pruneContext(group, Date.now() - CONTEXT_AGE_MS, CONTEXT_LINES);
  }

  stage(event, group, kind) {
    try {
      if (!this.state.stage(event, group, kind)) return;
      this.prune(group);
      this.clock.staged(group, kind);
    } catch (error) {
      this.onError(error);
    }
  }

  sent(group, at) { this.clock.sent(group, at); }

  resume() {
    const batches = new Map();
    for (const { group_id: group, kind } of this.state.pendingKinds()) {
      if (!batches.has(group)) batches.set(group, new Set());
      batches.get(group).add(kind);
    }
    for (const [group, kinds] of batches) this.clock.resume(group, kinds);
  }

  stop() { this.clock.stop(); }
}
