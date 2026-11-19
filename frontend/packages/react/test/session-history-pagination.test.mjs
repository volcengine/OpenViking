import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, StrictMode } from 'react';
import { VikingBotSessionActions } from '@openviking/react/components/viking-bot';
import { mount } from './helpers/module-dom.mjs';

const session = (id) => ({ id, title: id });
const tree = (props) =>
  h(
    StrictMode,
    null,
    h(VikingBotSessionActions, {
      sessions: {
        hasMore: true,
        items: [session('first')],
        currentId: 'active',
        onSelect() {},
        onNew() {},
        ...props,
      },
    }),
  );
const click = (button) => act(async () => button.click());
const trigger = (view) =>
  view.container.querySelector('button[aria-label="Session history"]');
const loadMore = () =>
  document.querySelector('button.ov-viking-bot__history-load-more');

test('opening history seeks the current session one page at a time', async () => {
  const pages = [Promise.withResolvers(), Promise.withResolvers()];
  const requests = [];
  const props = {
    onRename() {},
    onLoadMore: (...args) => {
      requests.push(args);
      return pages[requests.length - 1]?.promise;
    },
  };
  const view = await mount(tree(props));
  try {
    await click(trigger(view));
    assert.equal(requests.length, 1);
    assert.equal(loadMore().disabled, true);
    await click(loadMore());
    await view.render(tree({ ...props, items: [session('second')] }));
    assert.equal(requests.length, 1);
    await act(async () => pages[0].resolve());
    assert.equal(requests.length, 2);

    await view.render(tree({ ...props, items: [session('active')] }));
    await act(async () => pages[1].resolve());
    assert.equal(requests.length, 2);
    assert.equal(
      document.querySelector('[role="menuitem"][aria-current="true"]')
        ?.textContent,
      'active',
    );
    assert.equal(loadMore().disabled, false);
    await click(loadMore());
    assert.deepEqual(requests, [[], [], []]);
  } finally {
    pages.forEach(({ resolve }) => resolve());
    await view.unmount();
  }
});

test('unchanged history pages stop the automatic seek but allow manual retry', async () => {
  const pending = Promise.withResolvers();
  const requests = [];
  const props = {
    scopeKey: 'account-1',
    onLoadMore: () => assert.fail('the opt-in loader takes precedence'),
    onLoadMoreWithSignal: (signal) => {
      requests.push(signal);
      if (requests.length === 2) return pending.promise;
    },
  };
  const view = await mount(tree(props));
  try {
    await click(trigger(view));
    assert.equal(requests.length, 1);

    await view.render(tree({ ...props, items: [session('first')] }));
    assert.equal(requests.length, 1);
    await click(loadMore());
    assert.equal(requests.length, 2);
    assert.equal(loadMore().disabled, true);

    await view.render(
      tree({ ...props, scopeKey: 'account-2', hasMore: false }),
    );
    assert.equal(requests[1].aborted, true);
    await act(async () => pending.reject(new Error('late page error')));
    assert.equal(document.querySelector('[role="alert"]'), null);
  } finally {
    pending.resolve();
    await view.unmount();
  }
});

test('a page error is shown and closing history cancels a retry', async () => {
  const retry = Promise.withResolvers();
  const requests = [];
  const props = {
    onLoadMoreWithSignal: (signal) => {
      requests.push(signal);
      if (requests.length === 1) throw new Error('Page unavailable');
      if (requests.length === 2) return retry.promise;
    },
  };
  const view = await mount(tree(props));
  try {
    await click(trigger(view));
    assert.equal(
      document.querySelector('[role="alert"]').textContent,
      'Page unavailable',
    );
    assert.equal(requests.length, 1);
    await click(loadMore());
    assert.equal(requests.length, 2);
    await click(trigger(view));
    assert.equal(requests[1].aborted, true);
    assert.equal(loadMore(), null);
    await act(async () => retry.resolve());

    await click(trigger(view));
    assert.equal(requests.length, 3);
    assert.equal(document.querySelector('[role="alert"]'), null);
  } finally {
    retry.resolve();
    await view.unmount();
  }
});
