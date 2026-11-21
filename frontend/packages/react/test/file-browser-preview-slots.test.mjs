import { waitForEditor, setEditor } from './helpers/preview-editor-dom.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { FileBrowser } from '@openviking/react/modules/file-browser';
import { OpenVikingProvider } from '@openviking/react/client';
import { mount, ok, recordingFetch, settle } from './helpers/module-dom.mjs';

const uri = 'viking://resources/guide.md';
function fixture(isDir = false) {
  return recordingFetch(({ path, query }) => {
    if (path.endsWith('/fs/stat')) return ok({ isDir });
    if (path.endsWith('/fs/ls')) return ok([{ uri, isDir }]);
    if (/\/content\/(read|abstract|overview)$/.test(path))
      return ok(query.limit === '-1' ? '# Guide' : '');
    return ok([]);
  });
}
const tree = (fetch, props) =>
  h(OpenVikingProvider, { fetch }, h(FileBrowser, { uri, ...props }));

for (const isDir of [false, true]) {
  test(`preview slots replace, append and hide actions for ${isDir ? 'directories' : 'files'}`, async () => {
    const fetch = fixture(isDir);
    let copied;
    let actions;
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async (value) => (copied = value) },
    });
    const extraActions = h('button', { type: 'button' }, 'Host action');
    const view = await mount(tree(fetch, { previewSlots: { extraActions } }));
    const area = () =>
      view.container.querySelector('.ov-file-preview__header-actions');
    try {
      assert.ok(area().querySelector('.ov-file-preview__toolbar'));
      assert.match(area().textContent, /Host action/);
      const reads = fetch.calls.length;
      await view.render(
        tree(fetch, {
          previewSlots: {
            extraActions,
            actions: (value) => {
              actions = value;
              return h(
                'button',
                { type: 'button', onClick: () => void value.copyPath() },
                'Host copy',
              );
            },
          },
        }),
      );
      assert.equal(area().textContent, 'Host copy');
      assert.equal(actions.canCopyPath, true);
      await act(async () => area().querySelector('button').click());
      assert.equal(copied, uri);
      await view.render(tree(fetch, { previewSlots: { actions: null } }));
      assert.equal(area(), null);
      assert.ok(view.container.querySelector('[aria-label="Copy path"]'));
      assert.equal(fetch.calls.length, reads);
      await view.render(tree(fetch, {}));
      assert.doesNotMatch(area()?.textContent ?? '', /Host/);
      assert.equal(
        Boolean(area()?.querySelector('.ov-file-preview__toolbar')),
        true,
      );
    } finally {
      await view.unmount();
    }
  });
}

test('custom preview editing retains drafts after failure and respects readOnly', async () => {
  let actions;
  let fail = true;
  let writes = 0;
  let snapshots = 0;
  const fallback = fixture();
  const fetch = (input, init) => {
    if (String(input).includes('/content/write')) {
      writes++;
      return fail
        ? Promise.resolve(
            new Response(
              JSON.stringify({
                status: 'error',
                error: { code: 'WRITE_FAILED', message: 'Try again' },
              }),
              { status: 503 },
            ),
          )
        : ok({});
    }
    if (String(input).includes('/snapshot/commit')) {
      snapshots++;
      return Promise.resolve(new Response('', { status: 503 }));
    }
    return fallback(input, init);
  };
  const previewSlots = {
    actions: (value) => {
      actions = value;
      return h('span', null, value.editing ? 'Host editing' : 'Host actions');
    },
  };
  const view = await mount(tree(fetch, { previewSlots }));
  try {
    assert.equal(actions.canEdit, true);
    await act(async () => actions.startEditing());
    await settle();
    assert.equal(actions.editing, true);
    assert.equal(
      view.container.querySelector('.ov-file-preview__edit-toolbar'),
      null,
    );
    const editor = await waitForEditor(view.container);
    assert.equal(editor.textContent, '# Guide');
    await setEditor(editor, '# Updated guide');
    const save = async () => {
      let result;
      await act(async () => {
        result = await actions.saveEditing();
      });
      return result;
    };
    assert.equal(await save(), false);
    assert.equal(snapshots, 0);
    await settle();
    assert.equal(view.container.querySelector('.cm-content'), editor);
    assert.equal(editor.textContent, '# Updated guide');
    assert.equal(actions.busy, false);
    fail = false;
    assert.equal(await save(), true);
    assert.equal(snapshots, 0);
    await settle();
    assert.equal(actions.editing, false);
    assert.equal(writes, 2);
    await view.render(tree(fetch, { previewSlots, readOnly: true }));
    assert.equal(actions.canEdit, false);
    await act(async () => actions.startEditing());
    assert.equal(view.container.querySelector('.cm-content'), null);
  } finally {
    await view.unmount();
  }
});
