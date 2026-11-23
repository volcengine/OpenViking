import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  act,
  button,
  click,
  fixture,
  initial,
  level,
  mount,
  open,
  ok,
  tree,
  writes,
} from './helpers/acl-dom.mjs';

for (const [locale, read, title, cancel] of [
  ['en-US', 'Can read', 'Stop inheriting parent permissions?', 'Cancel'],
  ['zh-CN', '可阅读', '停止继承上级权限？', '取消'],
])
  test(`${locale} stopping inheritance opens a separate dialog and cancellation preserves the permission list`, async () => {
    const fetch = fixture();
    const view = await mount(tree(fetch, { locale }));
    try {
      const permissions = await open();
      const entries = permissions.querySelector(
        '.ov-file-browser-acl__entries',
      );
      await level('user:alice', read);
      const findConfirmation = () =>
        document.querySelector(`[role="dialog"][aria-label="${title}"]`);
      const confirmation = findConfirmation();
      assert.ok(confirmation, 'a separate confirmation dialog is shown');
      assert.notEqual(confirmation, permissions);
      assert.equal(permissions.contains(confirmation), false);
      assert.ok(
        entries.isConnected,
        'the permission list stays mounted behind it',
      );
      assert.equal(document.activeElement.textContent, cancel);
      assert.equal(writes(fetch).length, 0);
      await click(button(cancel, confirmation));
      assert.equal(document.contains(confirmation), false);
      assert.ok(permissions.isConnected);
      assert.equal(
        permissions.querySelector('.ov-file-browser-acl__entries'),
        entries,
      );
      assert.equal(writes(fetch).length, 0);
      await level('user:alice', read);
      await act(() =>
        findConfirmation().dispatchEvent(
          new window.KeyboardEvent('keydown', {
            key: 'Escape',
            bubbles: true,
            cancelable: true,
          }),
        ),
      );
      assert.equal(findConfirmation(), null);
      assert.ok(permissions.isConnected, 'Escape closes only the confirmation');
      assert.equal(writes(fetch).length, 0);
    } finally {
      await view.unmount();
    }
  });

test('confirming the separate dialog sends the existing restricted request once', async () => {
  const fetch = fixture();
  const view = await mount(tree(fetch));
  try {
    const permissions = await open();
    await level('user:alice', 'Can read');
    const confirmation = document.querySelector(
      '[role="dialog"][aria-label="Stop inheriting parent permissions?"]',
    );
    const confirm = button('Confirm', confirmation);
    await act(async () => {
      confirm.click();
      confirm.click();
    });
    assert.equal(writes(fetch).length, 1);
    assert.equal(writes(fetch)[0].init.method, 'PUT');
    assert.deepEqual(writes(fetch)[0].body, {
      uri: 'viking://resources/handbook',
      acl_mode: 'restricted',
      entries: [
        { principal: 'group:research', level: 'read' },
        { principal: 'user:alice', level: 'read' },
        { principal: 'user:bob', level: 'write' },
      ],
    });
    assert.equal(document.contains(confirmation), false);
    assert.ok(permissions.isConnected);
  } finally {
    await view.unmount();
  }
});

test('restoring inheritance keeps the existing single-dialog confirmation', async () => {
  const fetch = fixture((call) =>
    call.path.endsWith('/acl') && call.init.method === 'GET'
      ? ok({ ...initial(), acl_mode: 'restricted' })
      : undefined,
  );
  const view = await mount(tree(fetch));
  try {
    const permissions = await open();
    await click(button('Restore inheritance', permissions));
    assert.ok(permissions.querySelector('.ov-file-browser-acl__confirmation'));
    assert.equal(document.querySelectorAll('[role="dialog"]').length, 1);
    await click(button('Cancel', permissions));
    assert.equal(writes(fetch).length, 0);
  } finally {
    await view.unmount();
  }
});
