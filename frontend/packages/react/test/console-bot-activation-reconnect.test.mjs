import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from 'react';
import {
  deferred,
  mountActivation,
} from './helpers/console-bot-activation.mjs';

for (const operation of ['start', 'retry']) {
  test(`a host-seeded activation refreshes after Activity interrupts ${operation}`, async () => {
    const pending = deferred();
    let interruptedSignal;
    let eligibilityReads = 0;
    let statusReads = 0;
    let starts = 0;
    const props = {
      enabled: true,
      activation: {
        initialState:
          operation === 'start'
            ? { status: 'not-started' }
            : { status: 'error', error: new Error('Status unavailable') },
        async isEligible() {
          eligibilityReads++;
          return true;
        },
        async getStatus(signal) {
          statusReads++;
          if (operation === 'retry' && statusReads === 1) {
            interruptedSignal = signal;
            return pending.promise;
          }
          return 'ready';
        },
        async start(signal) {
          starts++;
          interruptedSignal = signal;
          await pending.promise;
        },
      },
    };
    const host = await mountActivation(props, 'visible');
    try {
      assert.deepEqual([eligibilityReads, statusReads, starts], [0, 0, 0]);
      await act(async () => host.lifecycle[operation]());
      assert.equal(
        host.lifecycle.state.status,
        operation === 'start' ? 'starting' : 'checking',
      );

      await host.render(props, 'hidden');
      assert.equal(interruptedSignal.aborted, true);
      await host.render(props, 'visible');
      assert.equal(host.lifecycle.state.status, 'ready');
      const expected = operation === 'start' ? [1, 1, 1] : [2, 2, 0];
      assert.deepEqual([eligibilityReads, statusReads, starts], expected);

      await act(async () => pending.resolve('not-started'));
      assert.equal(host.lifecycle.state.status, 'ready');
      await host.render(props, 'hidden');
      await host.render(props, 'visible');
      assert.deepEqual([eligibilityReads, statusReads, starts], expected);
    } finally {
      await host.unmount();
    }
  });
}
