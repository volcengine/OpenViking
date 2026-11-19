import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import { settle } from './helpers/module-dom.mjs';
import {
  deferred,
  mountActivation,
} from './helpers/console-bot-activation.mjs';

test('eligibility lookup failures block access until retry confirms eligibility', async () => {
  const error = new Error('Could not read the library');
  let collections = 0;
  let reads = 0;
  const host = await mountActivation({
    enabled: true,
    activation: {
      async isEligible() {
        if (++collections === 1) throw error;
        return true;
      },
      async getStatus() {
        reads++;
        return 'ready';
      },
      async start() {
        assert.fail('Looking up the library must not start the Bot');
      },
    },
  });
  try {
    assert.deepEqual(host.lifecycle.state, {
      status: 'collection-error',
      error,
    });
    assert.equal(reads, 0);
    await act(async () => host.lifecycle.retry());
    assert.equal(host.lifecycle.state.status, 'ready');
    assert.equal(reads, 1);
  } finally {
    await host.unmount();
  }
});

test('retry preserves the failed stage and does not start again after a refresh error', async () => {
  const loadError = new Error('Could not read initial status');
  const startError = new Error('Could not start');
  const refreshError = new Error('Could not read updated status');
  let starts = 0;
  let reads = 0;
  const activation = {
    async isEligible() {
      return true;
    },
    async getStatus() {
      reads++;
      if (reads === 1) throw loadError;
      if (reads === 4) throw refreshError;
      return reads <= 3 ? 'not-started' : 'ready';
    },
    async start() {
      if (++starts === 1) throw startError;
    },
  };
  const host = await mountActivation({ enabled: true, activation });
  try {
    assert.deepEqual(host.lifecycle.state, {
      status: 'load-error',
      error: loadError,
    });
    await act(async () => host.lifecycle.retry());
    await settle();
    assert.equal(host.lifecycle.state.status, 'not-started');
    assert.equal(starts, 0);
    await act(async () => host.lifecycle.start());
    await settle();
    assert.deepEqual(host.lifecycle.state, {
      status: 'start-error',
      error: startError,
    });
    await act(async () => host.lifecycle.retry());
    await settle();
    assert.deepEqual(host.lifecycle.state, {
      status: 'load-error',
      error: refreshError,
    });
    await act(async () => host.lifecycle.retry());
    await settle();
    assert.equal(host.lifecycle.state.status, 'ready');
    assert.equal(starts, 2);
    assert.equal(reads, 5);
  } finally {
    await host.unmount();
  }
});

for (const actualStatus of ['ready', 'disabled']) {
  test(`does not repeat an ambiguous startup when status is already ${actualStatus}`, async () => {
    let collections = 0;
    let reads = 0;
    let starts = 0;
    const refreshed = deferred();
    const activation = {
      async isEligible() {
        if (++collections === 1) return true;
        return (await refreshed.promise) === 'ready';
      },
      async getStatus() {
        return reads++ === 0 ? 'not-started' : 'ready';
      },
      async start() {
        starts++;
        throw new Error('Response timed out');
      },
    };
    const host = await mountActivation({ enabled: true, activation });
    try {
      await act(async () => host.lifecycle.start());
      await settle();
      assert.equal(host.lifecycle.state.status, 'start-error');
      await act(async () => host.lifecycle.retry());
      assert.equal(host.lifecycle.state.status, 'checking');
      assert.equal(starts, 1);
      await act(async () => refreshed.resolve(actualStatus));
      await settle();
      assert.equal(host.lifecycle.state.status, actualStatus);
      assert.equal(starts, 1);
      assert.equal(collections, 2);
      assert.equal(reads, actualStatus === 'ready' ? 2 : 1);
    } finally {
      await host.unmount();
    }
  });
}
