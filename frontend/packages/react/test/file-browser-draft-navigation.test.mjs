import assert from 'node:assert/strict';
import test from 'node:test';
import { createRef } from 'react';
import {
  act,
  button,
  click,
  fixture,
  mount,
  ok,
  rootUri,
  settle,
  tree,
} from './helpers/preview-native-dom.mjs';
import { setEditor, waitForEditor } from './helpers/preview-editor-dom.mjs';

const current = `${rootUri}/current.txt`;
const next = `${rootUri}/next.txt`;
const draft = 'Unsaved changes';
const selected = (root) =>
  root.querySelector('[role="tab"][aria-selected="true"]')?.dataset.key;

async function setup() {
  const ref = createRef();
  const fetch = fixture(current, false, ({ path, uri }) => {
    if (path.endsWith('/fs/ls') && uri === rootUri)
      return ok([
        { uri: current, isDir: false },
        { uri: next, isDir: false },
      ]);
    if (path.endsWith('/fs/stat') && uri === next)
      return ok({ isDir: false, access: 'manage' });
    if (path.endsWith('/content/read'))
      return ok(uri === current ? 'Current content' : 'Next content');
  });
  const uriChanges = [];
  const view = await mount(
    tree(fetch, undefined, {
      ref,
      defaultUri: current,
      onUriChange: (uri) => uriChanges.push(uri),
    }),
  );
  await click(view.container.querySelector('button[aria-label="Edit"]'));
  await setEditor(await waitForEditor(view.container), draft);
  return { view, ref, uriChanges };
}

test('tree navigation waits for a dirty preview and Escape keeps the draft', async () => {
  const { view, uriChanges } = await setup();
  const row = () =>
    view.container.querySelector(`[data-file-tree-row-id="${next}"]`);
  try {
    await click(row());
    await act(async () =>
      button('Keep editing').dispatchEvent(
        new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
      ),
    );
    assert.deepEqual(uriChanges, []);
    assert.equal(selected(view.container), current);
    assert.equal((await waitForEditor(view.container)).textContent, draft);

    await click(row());
    await click(button('Discard and leave'));
    assert.deepEqual(uriChanges, [next]);
    assert.equal(selected(view.container), next);
    assert.equal(view.container.querySelector('.cm-content') === null, true);
  } finally {
    await view.unmount();
  }
});

test('the imperative open resolves only for the confirmed dirty navigation', async () => {
  const { view, ref, uriChanges } = await setup();
  const open = () => ref.current.openResource(next, { reveal: false });
  try {
    let first;
    await act(async () => {
      first = open();
    });
    assert.equal(await open(), false);
    await click(button('Keep editing'));
    assert.equal(await first, false);
    assert.deepEqual(uriChanges, []);

    let confirmed;
    await act(async () => {
      confirmed = open();
    });
    await click(button('Discard and leave'));
    assert.equal(await confirmed, true);
    await settle();
    assert.deepEqual(uriChanges, [next]);
    assert.equal(selected(view.container), next);
  } finally {
    await view.unmount();
  }
});
