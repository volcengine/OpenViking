// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { RuntimeAdapter } from '../src/runtime-adapter.mjs';

describe('RuntimeAdapter contract', () => {
  it('normalizes batch admission for every transport implementation', () => {
    const calls = [];
    const adapter = new RuntimeAdapter('test', {
      deliverBatch(items) { calls.push(['enqueue', items]); return true; },
      tryStartBatch(items) { calls.push(['claim', items]); return false; },
    });
    const batch = [{ text: 'hello', seq: 1 }];
    assert.deepEqual(adapter.submit(batch), { accepted: true, disposition: 'enqueued' });
    assert.deepEqual(adapter.submit(batch, { admission: 'claim-if-idle' }), {
      accepted: false,
      reason: 'busy',
    });
    assert.deepEqual(calls, [['enqueue', batch], ['claim', batch]]);
    assert.deepEqual(new RuntimeAdapter('partial', {}).submit(batch), {
      accepted: false,
      reason: 'unsupported-admission',
    });
  });

  it('requires the same prepared session contract from every runtime', async () => {
    const native = new RuntimeAdapter('legacy', {
      async rotateSession() { return 'thread-new'; },
    });
    await assert.rejects(() => native.prepareSession(), /prepared session rotation/);

    const managedCalls = [];
    const managed = new RuntimeAdapter('volc', {
      async prepareSessionRotation() { return { id: 'sesn-new' }; },
      async commitSessionRotation(prepared, options) {
        managedCalls.push([prepared, options]);
        return prepared.id;
      },
    });
    const managedPlan = await managed.prepareSession();
    assert.equal(managedPlan.kind, 'prepared');
    assert.equal(await managed.commitSession(managedPlan, { persist: false }), 'sesn-new');
    assert.deepEqual(managedCalls, [[{ id: 'sesn-new' }, { persist: false }]]);
  });

  it('sets the cognition fields of the runtime it wraps', () => {
    const implementation = {};
    const adapter = new RuntimeAdapter('test', implementation);
    adapter.applyCognition({ epoch: 3, authorityToken: 'token-next', syncedThroughSeq: 0 });
    assert.deepEqual(implementation, {
      cognitionEpoch: 3,
      authorityToken: 'token-next',
      syncedThroughSeq: 0,
    });
  });
});
