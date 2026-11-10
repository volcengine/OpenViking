// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from 'node:test';
import { ActionService } from '../src/actions/service.mjs';
import { IdleLivenessExtension } from '../src/extensions/idle-liveness.mjs';
import { Store } from '../src/store.mjs';
import { fakeConnectors } from './action-fixture.mjs';
import { chatEvent } from './event-fixture.mjs';

const quiet = { debug() {}, info() {}, warn() {}, error() {}, child() { return this; } };

function harness({ thresholdMs = 600_000, enabled = true } = {}) {
  let now = 0;
  const extension = new IdleLivenessExtension({
    thresholdMs,
    enabled,
    now: () => now,
  });
  return {
    extension,
    advance(ms) { now += ms; },
  };
}

describe('idle liveness UX hint', () => {
  it('offers a hint after the delivery idle threshold without creating a reaction', () => {
    const state = harness();
    state.advance(601_000);

    assert.deepEqual(
      state.extension.beforeInbound({}, { seq: 7 }),
      { idleForMs: 601_000 },
    );
  });

  it('does not consume the idle window when the model silently ignores a message', () => {
    const state = harness();
    state.advance(601_000);

    assert.deepEqual(state.extension.beforeInbound({}, { seq: 8 }), { idleForMs: 601_000 });
    state.advance(1_000);
    assert.deepEqual(state.extension.beforeInbound({}, { seq: 9 }), { idleForMs: 602_000 });

    assert.deepEqual(state.extension.beforeInbound({}, { seq: 10 }), { idleForMs: 602_000 });
  });

  it('resets only after a real delivery and skips hints while the model is busy', () => {
    const state = harness();
    state.advance(601_000);
    assert.equal(
      state.extension.beforeInbound({}, { seq: 11 }, { modelBusy: true }),
      null,
    );

    state.extension.touch();
    assert.equal(state.extension.beforeInbound({}, { seq: 12 }), null);
    state.advance(600_000);
    assert.deepEqual(state.extension.beforeInbound({}, { seq: 13 }), { idleForMs: 600_000 });
  });

  it('can be disabled without changing the delivery path', () => {
    const state = harness({ enabled: false });
    state.advance(1_000_000);
    assert.equal(state.extension.beforeInbound({}, { seq: 14 }), null);
  });
});

describe('idle liveness and explicit silence', () => {
  it('keeps the idle window open after stay_silent, and closes it after a delivery', async (t) => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'idle-silence-'));
    const store = new Store(path.join(directory, 'state.sqlite3'), quiet);
    t.after(() => { store.close(); fs.rmSync(directory, { recursive: true, force: true }); });
    store.initializeCognition('codex', 'token');
    const state = harness();
    const service = new ActionService({
      config: { workspace: directory, tempDir: directory, maxReplyChars: 1_000, channel: 'lark', explicitSilence: true },
      store, connectors: fakeConnectors(), liveness: state.extension, logger: quiet,
    });
    store.ingest(chatEvent({ messageId: 'om_idle12345678', chatId: 'oc_idle12345678' }));
    state.advance(601_000);
    await service.call('stay_silent', { decision_id: 'message:om_idle12345678' }, { authority: 'token' });
    assert.deepEqual(state.extension.beforeInbound({}, { seq: 20 }), { idleForMs: 601_000 });
    await service.call('send_message', { chat_id: 'oc_idle12345678', markdown: 'hi', decision_id: 'message:om_idle12345678' },
      { authority: 'token' });
    assert.equal(state.extension.beforeInbound({}, { seq: 21 }), null);
  });
});
