import './helpers/history-viewer-dom.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import { notificationContent } from './helpers/feedback-dom.mjs';
import {
  fixture,
  tree as resourceTree,
  mount,
  workflow,
  click,
  button,
  act,
  ok,
  openAction,
} from './helpers/preview-native-dom.mjs';
import { waitForEditor, setEditor } from './helpers/preview-editor-dom.mjs';

const rootUri = 'viking://user/alice/memories';
const tree = (fetch, uri, props = {}) =>
  resourceTree(fetch, uri, { rootUri, ...props });
const uri = `${rootUri}/guide.md`;
async function edit(view, value = '# Updated') {
  await click(view.container.querySelector('[aria-label="Edit"]'));
  await setEditor(await waitForEditor(view.container), value);
}
async function note(value) {
  const input = workflow().querySelector('input');
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    ).set.call(input, value);
    input.dispatchEvent(new window.Event('input', { bubbles: true }));
  });
  return input;
}
const failure = (message) =>
  Response.json(
    {
      status: 'error',
      error: { code: 'CONFLICT', message },
    },
    { status: 409 },
  );

test('save succeeds before snapshot choice; conflict retains description and retry appears in history', async () => {
  let finishWrite, finishSnapshot;
  let body = '# Initial';
  let entries = [];
  let locked = true;
  const fetch = fixture(uri, false, (call) => {
    if (call.path.endsWith('/content/read'))
      return ok(call.query.limit === '-1' ? body : '');
    if (call.path.endsWith('/content/write'))
      return new Promise((resolve) => {
        finishWrite = () => {
          body = call.body.content;
          resolve(ok({}));
        };
      });
    if (call.path.endsWith('/snapshot/log')) return ok(entries);
    if (call.path.endsWith('/snapshot/show'))
      return Promise.resolve(new Response(body));
    if (call.path.endsWith('/snapshot/commit')) {
      if (locked)
        return Promise.resolve(failure('A snapshot path is being processed'));
      return new Promise((resolve) => {
        finishSnapshot = () => {
          entries = [
            {
              oid: 'snapshot-1',
              message: call.body.message,
              author: { time_seconds: 1794268800 },
            },
          ];
          resolve(ok({ result: 'created', commit_oid: 'snapshot-1' }));
        };
      });
    }
  });
  const view = await mount(tree(fetch, uri));
  try {
    await edit(view);
    await click(button('Save', view.container));
    assert.equal(workflow(), null);
    assert.equal(fetch.countOf('/api/v1/snapshot/commit'), 0);
    await act(async () => finishWrite());
    assert.equal(view.container.querySelector('.cm-content'), null);
    assert.equal(body, '# Updated');
    assert.match(workflow().textContent, /The file is saved/);
    assert.equal(workflow().querySelector('input'), null);
    await click(button('Keep a snapshot', workflow()));
    assert.equal(button('Save snapshot', workflow()).disabled, true);
    await note('   ');
    assert.equal(button('Save snapshot', workflow()).disabled, true);
    const input = await note(' Update guide ');
    await click(button('Save snapshot', workflow()));
    assert.match(notificationContent()?.textContent ?? '', /being processed/);
    assert.equal(input.value, ' Update guide ');
    assert.equal(body, '# Updated');
    assert.equal(fetch.countOf('/api/v1/content/write'), 1);
    locked = false;
    await click(button('Save snapshot', workflow()));
    assert.equal(button('Cancel', workflow()).disabled, true);
    await click(button('Save snapshot', workflow()));
    assert.equal(fetch.countOf('/api/v1/snapshot/commit'), 2);
    await act(async () => finishSnapshot());
    assert.match(workflow().textContent, /Snapshot saved/);
    assert.deepEqual(
      fetch.calls
        .filter((c) => c.path.endsWith('/snapshot/commit'))
        .map((c) => c.body),
      [
        { message: 'Update guide', paths: [uri], branch: 'main' },
        { message: 'Update guide', paths: [uri], branch: 'main' },
      ],
    );
    await click(button('Done', workflow()));
    await openAction('Version history');
    assert.match(workflow().textContent, /# Updated/);
    assert.ok(fetch.countOf('/api/v1/snapshot/log') > 0);
    assert.equal(fetch.countOf('/api/v1/content/write'), 1);
  } finally {
    await view.unmount();
  }
});

for (const action of ['skip', 'cancel', 'noop']) {
  test(`saved files survive snapshot ${action}`, async () => {
    let body = '# Initial';
    const fetch = fixture(uri, false, (call) => {
      if (call.path.endsWith('/content/read'))
        return ok(call.query.limit === '-1' ? body : '');
      if (call.path.endsWith('/content/write')) {
        body = call.body.content;
        return ok({});
      }
      if (call.path.endsWith('/snapshot/commit')) return ok({ result: 'noop' });
    });
    const view = await mount(tree(fetch, uri));
    try {
      await edit(view);
      await click(button('Save', view.container));
      if (action === 'skip') await click(button('Skip', workflow()));
      else {
        await click(button('Keep a snapshot', workflow()));
        await note('Saved edit');
        if (action === 'cancel') await click(button('Cancel', workflow()));
        else {
          await click(button('Save snapshot', workflow()));
          assert.match(workflow().textContent, /no changes to snapshot/);
          await click(button('Done', workflow()));
        }
      }
      assert.equal(workflow(), null);
      assert.equal(body, '# Updated');
      assert.equal(fetch.countOf('/api/v1/content/write'), 1);
      assert.equal(
        fetch.countOf('/api/v1/snapshot/commit'),
        action === 'noop' ? 1 : 0,
      );
    } finally {
      await view.unmount();
    }
  });
}

test('write failure keeps the draft and never offers a snapshot', async () => {
  const fetch = fixture(uri, false, (call) => {
    if (call.path.endsWith('/content/read'))
      return ok(call.query.limit === '-1' ? '# Initial' : '');
    if (call.path.endsWith('/content/write'))
      return Promise.resolve(failure('Write failed'));
  });
  const view = await mount(tree(fetch, uri));
  try {
    await edit(view);
    await click(button('Save', view.container));
    assert.equal(workflow(), null);
    assert.equal(
      (await waitForEditor(view.container)).textContent,
      '# Updated',
    );
    assert.equal(fetch.countOf('/api/v1/snapshot/commit'), 0);
  } finally {
    await view.unmount();
  }
});
