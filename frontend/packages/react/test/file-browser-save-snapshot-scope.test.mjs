import assert from 'node:assert/strict';
import test from 'node:test';
import {
  fixture,
  tree as resourceTree,
  mount,
  rootUri as resourceRoot,
  workflow,
  click,
  button,
  act,
  ok,
} from './helpers/preview-native-dom.mjs';
import { waitForEditor, setEditor } from './helpers/preview-editor-dom.mjs';

const rootUri = 'viking://user/alice/memories';
const tree = (fetch, uri, props = {}) =>
  resourceTree(fetch, uri, { rootUri, ...props });
const uri = `${rootUri}/guide.md`;
async function save(view) {
  await click(view.container.querySelector('[aria-label="Edit"]'));
  await setEditor(await waitForEditor(view.container), '# Updated');
  await click(button('Save', view.container));
}
async function fill(value) {
  const input = workflow().querySelector('input');
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    ).set.call(input, value);
    input.dispatchEvent(new window.Event('input', { bubbles: true }));
  });
}

for (const change of ['resource', 'account', 'permission']) {
  test(`late file-save success cannot open a snapshot choice after ${change} changes`, async () => {
    let finish;
    const fetch = fixture(uri, false, (call) => {
      if (call.path.endsWith('/content/read'))
        return ok(call.query.limit === '-1' ? '# Initial' : '');
      if (call.path.endsWith('/content/write'))
        return new Promise((resolve) => {
          finish = () => resolve(ok({}));
        });
    });
    const view = await mount(tree(fetch, uri));
    try {
      await save(view);
      await view.render(
        tree(
          fetch,
          change === 'resource' ? `${rootUri}/other.md` : uri,
          change === 'account'
            ? { requestOptions: { accountId: 'other' } }
            : change === 'permission'
              ? { readOnly: true }
              : {},
        ),
      );
      await act(async () => finish());
      assert.equal(workflow(), null);
      assert.equal(fetch.countOf('/api/v1/snapshot/commit'), 0);
    } finally {
      await view.unmount();
    }
  });
}

test('snapshot draft survives locale and label changes; switching account aborts submission', async () => {
  let finish;
  const fetch = fixture(uri, false, (call) => {
    if (call.path.endsWith('/content/read'))
      return ok(call.query.limit === '-1' ? '# Initial' : '');
    if (call.path.endsWith('/snapshot/commit'))
      return new Promise((resolve) => {
        finish = () => resolve(ok({ result: 'created' }));
      });
  });
  const view = await mount(tree(fetch, uri));
  try {
    await save(view);
    await click(button('Keep a snapshot', workflow()));
    await fill('Keep this description');
    await view.render(
      tree(fetch, uri, {
        locale: 'zh-CN',
        labels: {
          workflows: { snapshotFileSaved: '', saveSnapshot: undefined },
        },
      }),
    );
    assert.equal(
      workflow().querySelector('input').value,
      'Keep this description',
    );
    assert.doesNotMatch(workflow().textContent, /文件已保存/);
    await click(button('保存快照', workflow()));
    const call = fetch.calls.find((c) => c.path.endsWith('/snapshot/commit'));
    assert.equal(call.init.signal.aborted, false);
    await view.render(
      tree(fetch, uri, { requestOptions: { accountId: 'other' } }),
    );
    assert.equal(call.init.signal.aborted, true);
    assert.equal(workflow(), null);
    await act(async () => finish());
    assert.equal(workflow(), null);
    await save(view);
    assert.match(workflow().textContent, /Keep a snapshot/);
  } finally {
    await view.unmount();
  }
});

test('Wiki compile output saves with automatic versioning and no manual snapshot choice', async () => {
  const target = `${resourceRoot}/wiki`;
  const file = `${target}/guide.md`;
  const fetch = fixture(
    file,
    false,
    (call) => {
      if (call.path.endsWith('/fs/stat'))
        return ok({
          isDir: call.uri !== file,
          access: 'manage',
          content_kind: 'wiki',
        });
      if (call.path.endsWith('/content/read'))
        return ok(call.query.limit === '-1' ? '# Wiki' : '');
    },
    [{ to: target }],
  );
  const view = await mount(resourceTree(fetch, file));
  try {
    await save(view);
    assert.equal(workflow(), null);
    const write = fetch.calls.find((c) => c.path.endsWith('/content/write'));
    assert.equal(write.body.resolve_conflicts, true);
    assert.equal(write.body.use_snapshot, true);
    assert.equal('edit_source' in write.body, false);
    assert.equal(fetch.countOf('/api/v1/snapshot/commit'), 0);
  } finally {
    await view.unmount();
  }
});

test('ordinary resources save content without manual snapshot or history controls', async () => {
  const file = `${resourceRoot}/guide.md`;
  const fetch = fixture(file, false, (call) => {
    if (call.path.endsWith('/content/read'))
      return ok(call.query.limit === '-1' ? '# Initial' : '');
  });
  const view = await mount(resourceTree(fetch, file));
  try {
    await save(view);
    assert.equal(workflow(), null);
    assert.equal(fetch.countOf('/api/v1/content/write'), 1);
    assert.equal(fetch.countOf('/api/v1/snapshot/commit'), 0);
    assert.equal(
      view.container.querySelector('[aria-label="Version history"]'),
      null,
    );
  } finally {
    await view.unmount();
  }
});
