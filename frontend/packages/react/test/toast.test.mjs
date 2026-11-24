import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, StrictMode } from 'react';
import { useToast, useNotification } from './helpers/internal-hooks.mjs';
import { mount } from './helpers/module-dom.mjs';
import { wait } from './helpers/notification-host.mjs';

function Host({ capture, notification = false }) {
  const useFeedback = notification ? useNotification : useToast;
  const [api, holder] = useFeedback();
  capture(api);
  return h('div', null, h('button', null, 'Trigger'), holder);
}
const cards = () => [...document.querySelectorAll('.ov-toast')];

test('toast loading persists, updates in place and stays isolated from other instances and notifications', async () => {
  const apis = [];
  const view = await mount(
    h(
      StrictMode,
      null,
      ...[0, 1, 2].map((index) =>
        h(Host, {
          key: index,
          notification: index === 2,
          capture: (api) => {
            apis[index] = api;
          },
        }),
      ),
    ),
  );
  try {
    const api = apis[0];
    await act(() => {
      api.loading({ id: 'operation', content: 'Checking files' });
      apis[1].warning({
        id: 'operation',
        content: 'Other instance',
        duration: 0,
      });
      apis[2].error({ id: 'operation', content: 'Backend error', duration: 0 });
    });
    const card = cards()[0];
    assert.equal(card.getAttribute('aria-busy'), 'true');
    assert.ok(card.querySelector('.ov-loading-icon[aria-hidden="true"]'));
    await wait(3200);
    assert.equal(
      cards()[0],
      card,
      'loading does not inherit the three-second timeout',
    );
    await act(() =>
      api.success({ id: 'operation', content: 'Checked', duration: 0 }),
    );
    assert.equal(cards()[0], card);
    assert.equal(card.classList.contains('ov-toast--success'), true);
    assert.equal(card.getAttribute('aria-busy'), null);
    assert.equal(Boolean(card.querySelector('.ov-loading-icon')), false);
    assert.equal(apis[0], api);
    await act(() =>
      api.loading({
        id: 'operation',
        content: 'Custom',
        icon: h('span', null, 'Icon'),
      }),
    );
    assert.equal(Boolean(card.querySelector('.ov-loading-icon')), false);
    assert.match(card.textContent, /IconCustom/);
    await act(() =>
      api.loading({ id: 'operation', content: 'Hidden icon', showIcon: false }),
    );
    assert.equal(Boolean(card.querySelector('.ov-toast__icon')), false);
    await act(() => api.remove('operation'));
    await wait(240);
    assert.equal(cards().length, 1);
    assert.equal(cards()[0].textContent, 'Other instance');
    assert.equal(
      document.querySelector('.ov-notification').textContent,
      'Backend error',
    );
  } finally {
    await view.unmount();
  }
  assert.equal(Boolean(document.querySelector('.ov-toast-stack')), false);
});

test('toast timers pause on hover and focus, respect explicit loading duration, and preserve dismissal callbacks', async () => {
  let api;
  const events = [];
  const view = await mount(
    h(Host, {
      capture: (value) => {
        api = value;
      },
    }),
  );
  try {
    const trigger = view.container.querySelector('button');
    trigger.focus();
    await act(() => api.loading({ content: 'Timed', duration: 40 }));
    assert.equal(document.activeElement, trigger, 'toasts never steal focus');
    const card = cards()[0];
    await act(() =>
      card.dispatchEvent(new window.MouseEvent('mouseover', { bubbles: true })),
    );
    await wait(80);
    assert.equal(cards()[0], card);
    await act(() => {
      card.querySelector('button').focus();
      card.dispatchEvent(
        new window.MouseEvent('mouseout', {
          bubbles: true,
          relatedTarget: document.body,
        }),
      );
    });
    await wait(80);
    assert.equal(cards()[0], card);
    await act(() => trigger.focus());
    await wait(80);
    await wait(240);
    assert.equal(cards().length, 0);
    await act(() =>
      api.error({
        content: 'Fix the input',
        duration: 0,
        onDismiss: () => events.push('dismiss'),
        onClose: () => events.push('close'),
      }),
    );
    await act(() => cards()[0].querySelector('button').focus());
    await act(() => cards()[0].querySelector('button').click());
    assert.equal(document.activeElement, trigger);
    assert.deepEqual(events, ['dismiss']);
    await wait(240);
    assert.deepEqual(events, ['dismiss', 'close']);
    await act(() =>
      api.info({
        content: 'Leaving',
        duration: 40,
        onClose: () => events.push('unmounted'),
      }),
    );
  } finally {
    await view.unmount();
  }
  await wait(100);
  assert.deepEqual(events, ['dismiss', 'close']);
});
