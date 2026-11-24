import assert from 'node:assert/strict';
import test from 'node:test';
import { act, click } from './helpers/command-console-dom.mjs';
import { mount } from './helpers/console-dom.mjs';

const tick = (delay = 10) =>
  act(() => new Promise((resolve) => setTimeout(resolve, delay)));
const errors = () => document.querySelectorAll('.ov-notification--error');
const unexpected = async () =>
  assert.fail('Unexpected network or start request');

for (const dismissed of [false, true]) {
  test(`activation errors retain their ${dismissed ? 'dismissed' : 'visible'} state across mode switches`, async () => {
    let reads = 0;
    const error = new Error('Library permission lookup failed');
    const host = await mount(unexpected, {
      locale: 'en-US',
      defaultMode: 'conversation',
      conversation: { messages: [], composer: false },
      chatOptions: {
        activation: {
          async isEligible() {
            reads++;
            throw error;
          },
          getStatus: unexpected,
          start: unexpected,
        },
      },
    });
    try {
      await tick();
      assert.equal(errors().length, 1);
      const initialReads = reads;
      if (dismissed) {
        const close = errors()[0].querySelector(
          'button[aria-label="Close notification"]',
        );
        assert.ok(close);
        await click(close);
        await tick(240);
        assert.equal(errors().length, 0);
      }

      await host.press('Terminal');
      await tick();
      assert.equal(errors().length, 0);
      await host.press('VikingBot');
      await tick();
      assert.equal(errors().length, dismissed ? 0 : 1);
      assert.equal(reads, initialReads);

      // A new failed attempt is reportable even if it throws the same Error.
      await host.press('Retry');
      await tick();
      assert.ok(reads > initialReads);
      assert.equal(errors().length, 1);
      assert.match(errors()[0].textContent, /Library permission lookup failed/);
    } finally {
      await host.dispose();
    }
  });
}
