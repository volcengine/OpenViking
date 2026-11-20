import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from './helpers/command-console-dom.mjs';
import { mount } from './helpers/console-dom.mjs';

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
const commandVisible = (host) =>
  host.container
    .querySelector('.ov-command-console')
    ?.closest('.ov-console__panel')?.hidden === false;
function assertBotActive(host) {
  assert.equal(
    host.container.querySelector('.ov-console__panel--conversation')?.hidden,
    false,
  );
  assert.equal(commandVisible(host), false);
}
const mountActivation = (activation, props = {}) =>
  mount(
    async () => assert.fail('Controlled empty chat must not request data'),
    {
      locale: 'en-US',
      defaultMode: 'conversation',
      conversation: { messages: [], composer: false },
      chatOptions: { activation },
      ...props,
    },
  );

for (const outcome of ['disabled', 'ready', 'not-started', 'error']) {
  test(`initial skeleton resolves directly to ${outcome}`, async () => {
    const eligible = deferred();
    const ready = deferred();
    let statusReads = 0;
    const host = await mountActivation({
      initialLoading: 'skeleton',
      isEligible: () => eligible.promise,
      async getStatus() {
        statusReads++;
        const value = await ready.promise;
        if (value === 'error') throw new Error('Status unavailable');
        return value;
      },
      start: async () => assert.fail('Loading must not start the Bot'),
    });
    const assertSkeleton = () => {
      const skeleton = host.container.querySelector('.ov-console-skeleton');
      assert.equal(skeleton?.getAttribute('aria-label'), 'Loading Console…');
      assert.equal(host.container.querySelector('.ov-console__panel'), null);
      assert.equal(host.container.querySelector('.ov-console__modes'), null);
    };
    try {
      assertSkeleton();
      await act(() => eligible.resolve(outcome !== 'disabled'));
      if (outcome !== 'disabled') {
        assertSkeleton();
        await act(() => ready.resolve(outcome));
        assert.equal(statusReads, 1);
        assertBotActive(host);
      } else {
        assert.equal(statusReads, 0);
        assert.equal(commandVisible(host), true);
        assert.equal(host.container.querySelector('.ov-console__modes'), null);
      }
      assert.equal(host.container.querySelector('.ov-console-skeleton'), null);
      if (outcome === 'ready')
        assert.ok(host.container.querySelector('.ov-viking-bot'));
      if (outcome === 'not-started')
        assert.match(host.text, /Enable VikingBot/);
      if (outcome === 'error') assert.match(host.text, /Retry/);
    } finally {
      await host.dispose();
    }
  });
}
