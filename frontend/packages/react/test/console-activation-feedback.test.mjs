import assert from 'node:assert/strict';
import test from 'node:test';
import { act } from './helpers/command-console-dom.mjs';
import { mount } from './helpers/console-dom.mjs';

const tick = () => act(() => new Promise((resolve) => setTimeout(resolve, 10)));
function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
const mountActivation = (activation) =>
  mount(
    async () => assert.fail('Controlled empty chat must not request data'),
    {
      locale: 'en-US',
      defaultMode: 'conversation',
      conversation: { messages: [], composer: false },
      chatOptions: { activation },
    },
  );
const announcement = (host) =>
  host.container.querySelector('.ov-console-activation__announcement');
const announceFrame = () =>
  act(
    () =>
      new Promise((resolve) => window.requestAnimationFrame(() => resolve())),
  );

test('eligibility failure has one error notification and a retry without a duplicate card alert', async () => {
  let failing = true;
  const host = await mountActivation({
    async isEligible() {
      if (failing) throw new Error('Library permission lookup failed');
      return true;
    },
    getStatus: async () => 'ready',
    start: async () =>
      assert.fail('Retrying eligibility must not start the Bot'),
  });
  try {
    await tick();
    const notices = document.querySelectorAll('.ov-notification--error');
    assert.equal(notices.length, 1);
    assert.match(notices[0].textContent, /Library permission lookup failed/);
    const card = host.container.querySelector('.ov-console-activation');
    assert.ok(card);
    assert.doesNotMatch(card.textContent, /Could not enable/);
    assert.equal(card.querySelector('[role="alert"]'), null);
    assert.equal(
      card.querySelector('.ov-console-activation__action')?.textContent,
      'Retry',
    );
    failing = false;
    await host.press('Retry');
    await tick();
    assert.ok(host.container.querySelector('.ov-viking-bot'));
  } finally {
    await host.dispose();
  }
});

test('the status announcement stays mounted outside busy content from checking through ready', async () => {
  const eligible = deferred();
  const ready = deferred();
  const host = await mountActivation({
    isEligible: () => eligible.promise,
    getStatus: () => ready.promise,
    start: async () => assert.fail('Checking must not start the Bot'),
  });
  try {
    const status = announcement(host);
    assert.ok(status);
    assert.equal(status.getAttribute('role'), 'status');
    await announceFrame();
    assert.match(status.textContent, /Checking VikingBot status/);
    assert.equal(status.closest('[aria-busy="true"]'), null);
    await act(() => eligible.resolve(true));
    await tick();
    assert.equal(announcement(host), status);
    assert.equal(status.closest('[aria-busy="true"]'), null);
    await act(() => ready.resolve('ready'));
    await tick();
    await announceFrame();
    assert.equal(announcement(host), status);
    assert.equal(status.isConnected, true);
    assert.equal(status.textContent, 'VikingBot is ready.');
    assert.equal(status.closest('[aria-busy="true"]'), null);
    assert.equal(
      host.container.querySelectorAll('.ov-console-activation__announcement')
        .length,
      1,
    );
  } finally {
    await host.dispose();
  }
});

test('Enabling describes the start request and Checking describes its readiness refresh', async () => {
  const started = deferred();
  const ready = deferred();
  let reads = 0;
  let starts = 0;
  const host = await mountActivation({
    isEligible: async () => true,
    getStatus: async () => (reads++ === 0 ? 'not-started' : ready.promise),
    async start() {
      starts++;
      await started.promise;
    },
  });
  try {
    await tick();
    await host.press('Enable VikingBot');
    await announceFrame();
    assert.equal(starts, 1);
    assert.match(
      host.container.querySelector('.ov-console-activation').textContent,
      /Enabling VikingBot/,
    );
    assert.match(announcement(host).textContent, /Enabling VikingBot/);
    await act(() => started.resolve());
    await tick();
    await announceFrame();
    const card = host.container.querySelector('.ov-console-activation');
    assert.match(card.textContent, /Checking VikingBot status/);
    assert.doesNotMatch(card.textContent, /Enabling VikingBot/);
    assert.match(announcement(host).textContent, /Checking VikingBot status/);
    assert.equal(starts, 1);
    assert.equal(reads, 2);
    await act(() => ready.resolve('ready'));
    await tick();
    await announceFrame();
    assert.equal(announcement(host).textContent, 'VikingBot is ready.');
  } finally {
    await host.dispose();
  }
});
