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

test('host menu workflows receive the current resource and failed commands can be retried', async () => {
  let history = 0;
  const targets = [];
  const fetch = fixture(() => ok({ uri }));
  const view = await mount(
    tree(fetch, {
      previewActions: {
        onHistory: async (target) => {
          targets.push(target);
          if (++history === 1) throw new Error('Try again');
        },
        onImportPack: (target) => {
          targets.push(target);
        },
        onExportPack: (target) => {
          targets.push(target);
        },
      },
    }),
  );
  try {
    await click(more());
    assert.deepEqual(
      [...document.querySelectorAll('[role="menuitem"]')].map((node) =>
        node.textContent.trim(),
      ),
      [
        'Version history',
        'Import .ovpack',
        'Export .ovpack',
        'Delete document',
      ],
    );
    await click(menuItem('Version history'));
    assert.match(
      document.querySelector('[role="alert"]').textContent,
      /Try again/,
    );
    assert.equal(more().disabled, false);
    await click(more());
    await click(menuItem('Version history'));
    assert.equal(document.querySelector('[role="alert"]'), null);
    await click(more());
    await click(menuItem('Import .ovpack'));
    await click(more());
    await click(menuItem('Export .ovpack'));
    assert.deepEqual(targets, [uri, uri, parent, uri]);
  } finally {
    await view.unmount();
  }
});

test('account changes abort an in-flight deletion and ignore its late success', async () => {
  let resolve;
  const deleted = [];
  const fetch = fixture(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const props = {
    previewActions: { onDelete: (target) => deleted.push(target) },
  };
  const view = await mount(tree(fetch, props));
  try {
    await openDelete();
    await click(button('Delete', dialog()));
    const request = fetch.calls.find((call) => call.path === '/api/v1/fs');
    await view.render(tree(fetch, props, 'b'));
    assert.equal(request.init.signal.aborted, true);
    assert.ok(!dialog() || dialog().getAttribute('aria-hidden') === 'true');
    await act(async () => resolve(await ok({ uri })));
    assert.deepEqual(deleted, []);
  } finally {
    await view.unmount();
  }
});

test('menu keyboard navigation skips disabled commands and Escape restores the trigger', async () => {
  const fetch = fixture(() => ok({ uri }));
  const view = await mount(
    tree(fetch, {
      previewActions: { onHistory: () => {} },
      previewSlots: {
        menuItems: [
          {
            key: 'disabled',
            label: 'Disabled',
            disabled: true,
            onSelect: () => {},
          },
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
    assert.equal(document.activeElement, menuItem('Version history'));
    await key(document.activeElement, 'End');
    assert.equal(document.activeElement, menuItem('Delete document'));
    await key(document.activeElement, 'ArrowUp');
    assert.equal(document.activeElement, menuItem('Version history'));
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
