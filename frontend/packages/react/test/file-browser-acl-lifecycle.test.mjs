import assert from 'node:assert/strict';
import test from 'node:test';
import { deferred } from './helpers/module-dom.mjs';
import {
  act,
  button,
  click,
  fixture,
  initial,
  level,
  mount,
  ok,
  open,
  settle,
  tree,
  writes,
} from './helpers/acl-dom.mjs';

test('pending save rejects close and duplicate mutations until it finishes', async () => {
  const response = deferred();
  const fetch = fixture((call) =>
    call.path.endsWith('/acl') && call.init.method === 'PUT'
      ? response.promise
      : undefined,
  );
  const view = await mount(tree(fetch));
  try {
    await open();
    await level('group:research', 'Can edit');
    const close = document.querySelector(
      '.ov-file-browser-acl .ov-modal__close',
    );
    await click(close);
    await act(async () =>
      close.dispatchEvent(
        new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
      ),
    );
    assert.ok(document.querySelector('.ov-file-browser-acl [role="dialog"]'));
    assert.equal(
      document.querySelector('button[aria-label^="user:bob:"]').disabled,
      true,
    );
    assert.equal(writes(fetch).length, 1);
    await act(async () => response.resolve(await ok(initial())));
    await settle();
    assert.equal(
      document.querySelector('button[aria-label^="user:bob:"]').disabled,
      false,
    );
  } finally {
    await view.unmount();
  }
});

test('account switch aborts a pending save and prevents stale change notification', async () => {
  const response = deferred();
  const changed = [];
  const acl = { onChange: (report) => changed.push(report) };
  const fetch = fixture((call) =>
    call.path.endsWith('/acl') && call.init.method === 'PUT'
      ? response.promise
      : undefined,
  );
  const view = await mount(tree(fetch, { acl }));
  try {
    await open();
    await level('group:research', 'Can edit');
    const signal = writes(fetch)[0].init.signal;
    await view.render(tree(fetch, { acl }, { accountId: 'second' }));
    assert.equal(signal.aborted, true);
    assert.equal(
      document.querySelector('.ov-file-browser-acl [role="dialog"]'),
      null,
    );
    await act(async () => response.resolve(await ok(initial())));
    await settle();
    assert.deepEqual(changed, []);
    assert.equal(writes(fetch).length, 1);
  } finally {
    await view.unmount();
  }
});

test('host read-only mode leaves the grants visible and disables every mutation', async () => {
  const fetch = fixture();
  const view = await mount(tree(fetch, { acl: { readOnly: true } }));
  try {
    await open();
    assert.match(document.body.textContent, /changes are disabled/);
    assert.equal(
      document.querySelector('.ov-file-browser-acl__add input').disabled,
      true,
    );
    assert.equal(
      document.querySelector('button[aria-label^="user:bob:"]').disabled,
      true,
    );
    assert.deepEqual(writes(fetch), []);
    await click(
      button('Can manage', document.querySelector('.ov-file-browser-acl')) ??
        document.querySelector('.ov-file-browser-acl .ov-modal__close'),
    );
  } finally {
    await view.unmount();
  }
});
