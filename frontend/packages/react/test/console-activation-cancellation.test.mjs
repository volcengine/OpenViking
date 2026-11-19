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
const propsFor = (activation, signal, scopeKey = 'initial') => ({
  locale: 'en-US',
  defaultMode: 'conversation',
  conversation: { messages: [], composer: false },
  chatOptions: { activation },
  requestOptions: { signal },
  scopeKey,
});
function assertBotActive(host) {
  assert.equal(host.button('VikingBot')?.getAttribute('aria-pressed'), 'true');
  assert.equal(
    host.container.querySelector('.ov-console__panel--conversation')?.hidden,
    false,
  );
  assert.equal(
    host.container
      .querySelector('.ov-command-console')
      ?.closest('.ov-console__panel')?.hidden,
    true,
  );
}
function assertCancelled(host) {
  assertBotActive(host);
  const card = host.container.querySelector('.ov-console-activation');
  assert.ok(card);
  assert.match(card.textContent, /VikingBot status check cancelled/);
  assert.equal(card.querySelector('.ov-loading-icon'), null);
  assert.notEqual(card.getAttribute('aria-busy'), 'true');
  assert.equal(card.querySelector('[aria-busy="true"]'), null);
  assert.equal(card.querySelector('button'), null);
  assert.equal(host.button('Enable VikingBot'), undefined);
  assert.equal(host.button('Retry'), undefined);
}
const unexpectedRequest = async () =>
  assert.fail('Controlled empty chat must not request data');

test('scope cancellation is settled, ignores late readiness, and resumes only with a fresh signal', async () => {
  const scope = new AbortController();
  const queries = [];
  const changes = [];
  let eligibilityCalls = 0;
  const activation = {
    async isEligible() {
      eligibilityCalls++;
      return true;
    },
    getStatus(signal) {
      const query = { ...deferred(), signal };
      queries.push(query);
      return query.promise;
    },
    start: async () => assert.fail('Cancellation must not start the Bot'),
  };
  const props = {
    ...propsFor(activation, scope.signal),
    onModeChange: (mode) => changes.push(mode),
  };
  const host = await mount(unexpectedRequest, props);
  try {
    await tick();
    assertBotActive(host);
    assert.ok(queries.length > 0);
    const original = queries.at(-1);
    await act(() => scope.abort());
    assert.equal(original.signal.aborted, true);
    assertCancelled(host);
    await act(() => original.resolve('ready'));
    await tick();
    assertCancelled(host);
    assert.equal(host.container.querySelector('.ov-viking-bot'), null);

    const callsAfterAbort = [eligibilityCalls, queries.length];
    await host.render({ ...props, scopeKey: 'same-cancelled-signal' });
    await tick();
    assertCancelled(host);
    assert.deepEqual([eligibilityCalls, queries.length], callsAfterAbort);

    const fresh = new AbortController();
    await host.render({
      ...props,
      scopeKey: 'fresh-scope',
      requestOptions: { signal: fresh.signal },
    });
    await tick();
    assertBotActive(host);
    const card = host.container.querySelector('.ov-console-activation');
    assert.equal(card?.getAttribute('aria-busy'), 'true');
    assert.ok(card.querySelector('.ov-loading-icon'));
    assert.ok(queries.length > callsAfterAbort[1]);
    const resumed = queries.at(-1);
    assert.equal(resumed.signal.aborted, false);
    await act(() => resumed.resolve('ready'));
    await tick();
    assertBotActive(host);
    assert.equal(host.container.querySelector('.ov-console-activation'), null);
    assert.ok(host.container.querySelector('.ov-viking-bot'));
    assert.deepEqual(changes, []);
  } finally {
    await host.dispose();
  }
});

test('an initially aborted scope shows static cancellation without querying', async () => {
  const scope = new AbortController();
  scope.abort();
  const unexpected = async () =>
    assert.fail('An aborted scope cannot query or start');
  const host = await mount(
    unexpectedRequest,
    propsFor(
      {
        isEligible: unexpected,
        getStatus: unexpected,
        start: unexpected,
      },
      scope.signal,
    ),
  );
  try {
    await tick();
    assertCancelled(host);
  } finally {
    await host.dispose();
  }
});
