import assert from 'node:assert/strict';
import test from 'node:test';
import {
  act,
  mount,
  ok,
  settle,
  click,
  button,
  uri,
  parent,
  fixture,
  tree,
  dialog,
  openDelete,
  failure,
} from './helpers/preview-actions-dom.mjs';

test('native deletion confirms the selected file, prevents duplicate requests and refreshes after success', async () => {
  let resolve;
  const fetch = fixture(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const deleted = [];
  const selected = [];
  const view = await mount(
    tree(fetch, {
      onUriChange: (next) => selected.push(next),
      previewActions: { onDelete: (target) => deleted.push(target) },
    }),
  );
  try {
    await openDelete();
    assert.match(dialog().textContent, /guide\.md/);
    assert.equal(
      fetch.calls.filter((call) => call.path === '/api/v1/fs').length,
      0,
    );
    await click(button('Delete', dialog()));
    assert.equal(button('Delete', dialog()).disabled, true);
    await click(button('Delete', dialog()));
    const writes = fetch.calls.filter((call) => call.path === '/api/v1/fs');
    assert.equal(writes.length, 1);
    assert.equal(writes[0].init.method, 'DELETE');
    assert.deepEqual(writes[0].query, { uri, wait: 'true' });
    await act(async () => resolve(await ok({ uri })));
    await settle();
    assert.deepEqual(deleted, [uri]);
    assert.equal(selected.at(-1), parent);
    assert.ok(
      fetch.calls.filter((call) => call.path.endsWith('/fs/ls')).length > 1,
    );
    assert.ok(!dialog() || dialog().getAttribute('aria-hidden') === 'true');
  } finally {
    await view.unmount();
  }
});

test('deletion failure retains the confirmation, supports retry and preserves it across language changes', async () => {
  let fail = true;
  const fetch = fixture(() =>
    fail ? Promise.resolve(failure()) : ok({ uri }),
  );
  const props = {
    previewActions: {
      onDelete: () => {
        throw new Error('host notification');
      },
    },
  };
  const view = await mount(tree(fetch, props));
  try {
    await openDelete();
    const reads = fetch.calls.length;
    await view.render(tree(fetch, { ...props, locale: 'zh-CN' }));
    assert.match(dialog().textContent, /删除文档？/);
    assert.equal(fetch.calls.length, reads);
    await click(button('删除', dialog()));
    assert.match(
      dialog().querySelector('[role="alert"]').textContent,
      /Try again/,
    );
    assert.equal(button('删除', dialog()).disabled, false);
    assert.equal(
      view.container.querySelector('button[aria-label="编辑"]') !== null,
      true,
    );
    fail = false;
    await click(button('删除', dialog()));
    await settle();
    assert.equal(
      document.querySelector('.ov-file-browser-delete [role="alert"]'),
      null,
    );
    assert.equal(
      fetch.calls.filter((call) => call.path === '/api/v1/fs').length,
      2,
    );
  } finally {
    await view.unmount();
  }
});

test('visibility, readOnly and ACL manage permission gate native deletion and custom contexts', async () => {
  const fetch = fixture(() => ok({ uri }), 'write');
  let context;
  const slots = {
    actions: (value) => {
      context = value;
      return null;
    },
  };
  const view = await mount(tree(fetch, { previewSlots: slots }));
  try {
    assert.equal(context.canDelete, true);
    await act(async () => context.deleteFile());
    assert.ok(dialog());
    await view.render(tree(fetch, { readOnly: true, previewSlots: slots }));
    assert.equal(context.canDelete, false);
    await act(async () => context.deleteFile());
    assert.ok(!dialog() || dialog().classList.contains('ov-modal--exit'));
    await view.render(tree(fetch, { acl: true, previewSlots: slots }));
    assert.equal(context.canDelete, false);
    await view.render(
      tree(fetch, {
        previewActions: { delete: false, edit: false },
        previewSlots: slots,
      }),
    );
    assert.equal(context.canDelete, false);
    assert.equal(context.canEdit, false);
    assert.equal(
      fetch.calls.filter((call) => call.path === '/api/v1/fs').length,
      0,
    );
  } finally {
    await view.unmount();
  }
});
