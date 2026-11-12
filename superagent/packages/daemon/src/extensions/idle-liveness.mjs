// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// A process-local hint only. The daemon never reacts on receipt: it tells the
// model how long outbound delivery has been idle, then lets the normal reply
// policy decide whether this message deserves work. Every successful delivery
// through the ActionService touches it; temporary reactions live there too.
export class IdleLivenessExtension {
  constructor({
    thresholdMs = 600_000,
    enabled = true,
    now = () => Date.now(),
  } = {}) {
    this.thresholdMs = thresholdMs;
    this.enabled = enabled && thresholdMs > 0;
    this.now = now;
    this.lastDeliveryAtMs = now();
  }

  touch(atMs = this.now()) {
    this.lastDeliveryAtMs = atMs;
  }

  beforeInbound(_event, _inbound, { modelBusy = false } = {}) {
    if (!this.enabled || modelBusy) return null;
    const idleForMs = Math.max(0, this.now() - this.lastDeliveryAtMs);
    return idleForMs >= this.thresholdMs ? { idleForMs } : null;
  }
}
