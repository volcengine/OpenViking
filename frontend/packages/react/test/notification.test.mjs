import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, StrictMode, useState } from 'react';
import {
  useNotification,
  OpenVikingProvider,
} from './helpers/internal-hooks.mjs';
import { mount } from './helpers/module-dom.mjs';
import { Host, cards, wait, close } from './helpers/notification-host.mjs';

test('notification updates a task in place, preserves custom content and isolates instances', async () => {
  const apis = [];
  const view = await mount(
    h(
      StrictMode,
      null,
      h(Host, {
        capture: (api) => {
          apis[0] = api;
        },
      }),
      h(Host, {
        capture: (api) => {
          apis[1] = api;
        },
      }),
    ),
  );
  let firstClosed = 0;
  let secondClosed = 0;
  function Details() {
    const [expanded, setExpanded] = useState(false);
    return h(
      'button',
      { onClick: () => setExpanded(!expanded) },
      expanded ? 'Expanded' : 'Details',
    );
  }
  try {
    const api = apis[0];
    await act(() => {
      api.info({
        id: 'task',
        title: 'Running',
        content: h(Details),
        duration: 0,
      });
      apis[1].info({
        id: 'task',
        content: 'Other account',
        duration: 0,
        onClose: () => secondClosed++,
      });
    });
    assert.equal(cards().length, 2);
    const card = cards()[0];
    await act(() =>
      card.querySelector('.ov-notification__content button').click(),
    );
    await act(() =>
      api.success({
        id: 'task',
        title: 'Completed',
        content: h(Details),
        duration: 0,
        onClose: () => firstClosed++,
      }),
    );
    assert.equal(cards()[0], card);
    assert.match(card.textContent, /CompletedExpanded/);
    assert.equal(apis[0], api, 'API is stable across updates');
    await wait(240);
    assert.equal(cards().length, 2);
    await close(card);
    assert.equal(cards().length, 1);
    assert.equal(firstClosed, 1);
    assert.equal(secondClosed, 0);
    assert.equal(cards()[0].textContent, 'Other account');
    await act(() => apis[1].clear());
    await wait(240);
    assert.equal(cards().length, 0);
    assert.equal(secondClosed, 1);
  } finally {
    await view.unmount();
  }
  assert.equal(document.querySelector('.ov-notification-stack'), null);
});

test('notification timers reset on ID updates, pause on hover and keyboard focus, and stop on unmount', async () => {
  let api;
  let closed = 0;
  const view = await mount(
    h(Host, {
      capture: (value) => {
        api = value;
      },
    }),
  );
  try {
    await act(() =>
      api.info({
        id: 'timer',
        content: h('button', null, 'Action'),
        duration: 80,
        onClose: () => closed++,
      }),
    );
    const card = cards()[0];
    await act(() =>
      card.dispatchEvent(new window.MouseEvent('mouseover', { bubbles: true })),
    );
    await wait(120);
    assert.equal(cards()[0], card);
    await act(() => {
      card.querySelector('.ov-notification__content button').focus();
      card.dispatchEvent(
        new window.MouseEvent('mouseout', {
          bubbles: true,
          relatedTarget: document.body,
        }),
      );
    });
    await wait(120);
    assert.equal(cards()[0], card);
    await act(() => view.container.querySelector('button').focus());
    await act(() =>
      api.success({
        id: 'timer',
        content: 'Persistent result',
        duration: 0,
        onClose: () => closed++,
      }),
    );
    await wait(120);
    assert.equal(cards()[0], card);
    await act(() =>
      api.error({
        id: 'timer',
        content: 'Temporary result',
        duration: 30,
        onClose: () => closed++,
      }),
    );
    await wait(70);
    await wait(240);
    assert.equal(cards().length, 0);
    assert.equal(closed, 1);
    await act(() =>
      api.info({ content: 'Leaving', duration: 30, onClose: () => closed++ }),
    );
  } finally {
    await view.unmount();
  }
  await wait(100);
  assert.equal(closed, 1);
});

test('notification reports dismissal before exit and preserves post-animation onClose', async () => {
  let api;
  const view = await mount(
    h(Host, {
      capture: (value) => {
        api = value;
      },
    }),
  );
  try {
    for (const timed of [false, true]) {
      const events = [];
      await act(() =>
        api.info({
          content: 'Dismissal lifecycle',
          duration: timed ? 30 : 0,
          onDismiss: () => events.push('dismiss'),
          onClose: () => events.push('closed'),
        }),
      );
      if (timed) {
        await wait(80);
      } else {
        await act(() =>
          cards()[0].querySelector('.ov-notification__close').click(),
        );
        assert.deepEqual(events, ['dismiss']);
        assert.equal(
          cards().length,
          1,
          'the notice remains during its exit animation',
        );
      }
      await wait(240);
      assert.deepEqual(events, ['dismiss', 'closed']);
      assert.equal(cards().length, 0);
    }
  } finally {
    await view.unmount();
  }
});
