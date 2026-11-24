import assert from 'node:assert/strict';
import test from 'node:test';
import { act, click } from './helpers/command-console-dom.mjs';
import { mount } from './helpers/console-dom.mjs';

const tick = () => act(() => new Promise((resolve) => setTimeout(resolve, 10)));
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((success, failure) => {
    resolve = success;
    reject = failure;
  });
  return { promise, resolve, reject };
}

for (const [outcome, ownsFocus] of [
  ['disabled', true],
  ['error', true],
  ['ready', true],
  ['disabled', false],
]) {
  test(`Retry preserves ${ownsFocus ? 'Bot' : 'external'} focus through ${outcome}`, async () => {
    const pending = deferred();
    const outside = document.createElement('button');
    document.body.append(outside);
    let retrying = false;
    const unexpected = async () => assert.fail('Unexpected network or start');
    const host = await mount(unexpected, {
      locale: 'en-US',
      defaultMode: 'conversation',
      conversation: { messages: [], composer: false },
      chatOptions: {
        activation: {
          async isEligible() {
            if (!retrying) throw new Error('Eligibility unavailable');
            return pending.promise;
          },
          getStatus: async () => 'ready',
          start: unexpected,
        },
      },
    });
    try {
      await tick();
      const panel = host.container.querySelector(
        '.ov-console__panel--conversation',
      );
      const retry = host.button('Retry');
      assert.ok(retry);
      await act(() => (ownsFocus ? retry : outside).focus());
      retrying = true;
      // The click helper focuses Retry; a programmatic retry preserves external focus.
      if (ownsFocus) await click(retry);
      else await act(() => retry.click());
      assert.equal(retry.isConnected, false);
      assert.equal(
        document.activeElement === (ownsFocus ? panel : outside),
        true,
      );
      assert.match(panel.textContent, /Checking VikingBot status/);

      if (outcome === 'error')
        await act(() => pending.reject(new Error('Still unavailable')));
      else await act(() => pending.resolve(outcome === 'ready'));
      await tick();

      if (outcome === 'disabled') {
        assert.equal(panel.isConnected, false);
        const input = host.container.querySelector(
          'textarea.ov-command-console__input',
        );
        assert.equal(
          document.activeElement === (ownsFocus ? input : outside),
          true,
        );
      } else {
        assert.equal(panel.isConnected, true);
        assert.equal(document.activeElement === panel, true);
        if (outcome === 'error') {
          assert.ok(host.button('Retry'));
          assert.equal(host.button('Retry') === retry, false);
        } else assert.ok(host.container.querySelector('.ov-viking-bot'));
      }
    } finally {
      await host.dispose();
      outside.remove();
    }
  });
}
