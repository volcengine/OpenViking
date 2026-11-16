import assert from 'node:assert/strict';
import { act, createElement as h } from 'react';
import { mount, setTextarea } from './module-dom.mjs';

export function fault() {
  const state = { broken: false, renders: 0 };
  state.read = () => {
    state.renders++;
    if (state.broken) throw new Error('intentional retry audit failure');
    return 'Recovered region';
  };
  state.Content = () => h('span', { 'data-recovered': true }, state.read());
  return state;
}

export const retrySlot = (reset) =>
  h(
    'button',
    {
      'data-host-retry': true,
      onClick: reset,
    },
    'Retry this region',
  );

export async function verifyRetry(t, view, preserve) {
  t.mock.method(console, 'error', () => {});
  const failure = fault();
  const render = () =>
    h(
      'main',
      null,
      h('textarea', { 'aria-label': 'Host draft', defaultValue: '' }),
      view(failure, { errorFallback: retrySlot }),
    );
  const host = await mount(render());
  try {
    const input = host.container.querySelector('[aria-label="Host draft"]');
    await setTextarea(input, 'Retained host draft');
    const sibling = preserve && host.container.querySelector(preserve);
    if (preserve) assert.ok(sibling, `missing healthy sibling: ${preserve}`);
    assert.equal(host.container.querySelector('[data-host-retry]'), null);
    failure.broken = true;
    await host.render(render());
    assert.ok(
      host.container.querySelector('[data-host-retry]'),
      'failure must reach the host function',
    );
    const attempts = failure.renders;
    failure.broken = false;
    await host.render(render());
    assert.ok(
      host.container.querySelector('[data-host-retry]'),
      'fixing props must not implicitly retry',
    );
    assert.equal(failure.renders, attempts, 'no retry until reset');
    await act(() => host.container.querySelector('[data-host-retry]').click());
    assert.ok(
      failure.renders > attempts,
      'reset must actually render the failed region again',
    );
    assert.equal(host.container.querySelector('[data-host-retry]'), null);
    assert.equal(
      host.container.querySelector('[aria-label="Host draft"]'),
      input,
    );
    assert.equal(input.value, 'Retained host draft');
    if (preserve) assert.equal(host.container.querySelector(preserve), sibling);
  } finally {
    await host.unmount();
  }
}
