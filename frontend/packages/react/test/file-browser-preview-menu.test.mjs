import { createElement as h } from 'react';
import assert from 'node:assert/strict';
import test from 'node:test';
import {
  act,
  mount,
  ok,
  click,
  button,
  uri,
  parent,
  fixture,
  tree,
  more,
  menuItem,
  dialog,
  openDelete,
} from './helpers/preview-actions-dom.mjs';

test('account changes abort an in-flight deletion and ignore its late success', async () => {
  let resolve;
  const fetch = fixture(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const props = {};
  const view = await mount(tree(fetch, props));
  try {
    await openDelete();
    await click(button('Delete', dialog()));
    const request = fetch.calls.find((call) => call.path === '/api/v1/fs');
    await view.render(tree(fetch, props, 'b'));
    assert.equal(request.init.signal.aborted, true);
    assert.ok(!dialog() || dialog().getAttribute('aria-hidden') === 'true');
    await act(async () => resolve(await ok({ uri })));
    assert.equal(
      fetch.calls.filter((call) => call.path === '/api/v1/fs').length,
      1,
    );
  } finally {
    await view.unmount();
  }
});

test('menu keyboard navigation skips disabled commands and Escape restores the trigger', async () => {
  const fetch = fixture(() => ok({ uri }));
  const view = await mount(
    tree(fetch, {
      previewSlots: {
        actionItems: (context) => [
          ...context.defaultActions,
          [
            {
              key: 'disabled',
              icon: h('svg'),
              label: 'Disabled',
              disabled: true,
              onClick: () => {},
            },
          ],
        ],
      },
    }),
  );
  const key = async (node, value) =>
    act(async () =>
      node.dispatchEvent(
        new window.KeyboardEvent('keydown', { key: value, bubbles: true }),
      ),
    );
  try {
    const trigger = more();
    await key(trigger, 'ArrowDown');
    assert.equal(document.activeElement, menuItem('Download'));
    await key(document.activeElement, 'End');
    assert.equal(document.activeElement, menuItem('Delete document'));
    await key(document.activeElement, 'ArrowUp');
    assert.equal(document.activeElement, menuItem('Export .ovpack'));
    await key(document.activeElement, 'Escape');
    assert.equal(document.querySelector('[role="menu"]'), null);
    assert.equal(document.activeElement, trigger);
    await key(trigger, 'ArrowDown');
    await key(document.activeElement, 'Tab');
    assert.equal(document.querySelector('[role="menu"]'), null);
  } finally {
    await view.unmount();
  }
});
