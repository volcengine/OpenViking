import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { mount } from './helpers/module-dom.mjs';
import { Host, cards, wait } from './helpers/notification-host.mjs';

test('loading notices use the built-in icon, persist and update to a terminal result in place', async () => {
  let api;
  const view = await mount(
    h(Host, {
      capture: (value) => {
        api = value;
      },
    }),
  );
  try {
    await act(() =>
      api.loading({ id: 'task', content: 'Working', duration: undefined }),
    );
    const card = cards()[0];
    assert.equal(card.getAttribute('aria-busy'), 'true');
    assert.equal(
      card.querySelector('.ov-loading-icon')?.getAttribute('aria-hidden'),
      'true',
    );
    await wait(3200);
    assert.equal(
      cards().length,
      1,
      'loading does not use the three-second default timeout',
    );
    await act(() =>
      api.success({ id: 'task', content: 'Completed', duration: 0 }),
    );
    assert.equal(cards()[0], card);
    assert.equal(card.classList.contains('ov-notification--success'), true);
    assert.equal(card.getAttribute('aria-busy'), null);
    assert.equal(card.querySelectorAll('.ov-loading-icon').length, 0);
    await act(() =>
      api.loading({
        id: 'task',
        content: 'Working',
        icon: h('span', null, 'Custom icon'),
      }),
    );
    assert.match(card.textContent, /Custom icon/);
    assert.equal(card.querySelectorAll('.ov-loading-icon').length, 0);
    await act(() =>
      api.loading({ id: 'task', content: 'Working', showIcon: false }),
    );
    assert.equal(card.querySelectorAll('.ov-notification__icon').length, 0);
  } finally {
    await view.unmount();
  }
});
