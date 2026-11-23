import assert from 'node:assert/strict';
import test from 'node:test';
import { mount } from './helpers/console-dom.mjs';

for (const status of ['ready', 'not-started', 'error']) {
  test(`Console renders the host's resolved ${status} state without repeating the initial lookup`, async () => {
    let lookups = 0;
    const host = await mount(
      async () => assert.fail('Controlled empty chat must not request data'),
      {
        defaultMode: 'conversation',
        conversation: { messages: [], composer: false },
        chatOptions: {
          activation: {
            initialState:
              status === 'error'
                ? { status, error: new Error('Status unavailable') }
                : { status },
            async isEligible() {
              lookups++;
              return true;
            },
            async getStatus() {
              lookups++;
              return 'ready';
            },
            start: async () => assert.fail('Loading must not start the Bot'),
          },
        },
      },
    );
    try {
      assert.equal(lookups, 0);
      assert.doesNotMatch(host.text, /Checking VikingBot status/);
      if (status === 'ready')
        assert.ok(host.container.querySelector('.ov-viking-bot'));
      if (status === 'not-started') assert.ok(host.button('Enable VikingBot'));
      if (status === 'error') {
        await host.press('Retry');
        assert.equal(lookups, 2);
        assert.ok(host.container.querySelector('.ov-viking-bot'));
      }
    } finally {
      await host.dispose();
    }
  });
}
