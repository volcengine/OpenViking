import { notificationError } from './helpers/feedback-dom.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h } from 'react';
import { EditorView } from '@codemirror/view';
import Prism from 'prismjs';
import { FilePreview } from '@openviking/react/components/file-preview';
import { mount } from './helpers/module-dom.mjs';
import { waitForEditor } from './helpers/preview-editor-dom.mjs';
const wait = () =>
  act(() => new Promise((resolve) => setTimeout(resolve, 130)));
const edit = async (root) => {
  await act(async () => root.querySelector('[aria-label="Edit"]').click());
  return EditorView.findFromDOM(await waitForEditor(root));
};

test('opening the editor does not rewrite untouched CRLF content', async () => {
  const content = 'const count = 1;\r\n// original line endings\r\n';
  const saved = [];
  const view = await mount(
    h(FilePreview, {
      tabs: [
        {
          kind: 'file',
          key: 'a',
          source: { content: '', fileName: 'a.js' },
          editing: {
            loadContent: async () => content,
            save: async (value) => {
              saved.push(value);
            },
          },
        },
      ],
    }),
  );
  try {
    await edit(view.container);
    await act(async () =>
      [...view.container.querySelectorAll('button')]
        .find((button) => button.textContent === 'Save')
        .click(),
    );
    assert.deepEqual(saved, [content]);
  } finally {
    await view.unmount();
  }
});

test('highlight failures retain draft recovery after the notification expires', async () => {
  const original = Prism.tokenize;
  const view = await mount(
    h(FilePreview, {
      tabs: [
        {
          kind: 'file',
          key: 'a',
          source: { content: '', fileName: 'a.js' },
          editing: {
            loadContent: async () => 'const draft = 1;',
            save: async () => {},
          },
        },
      ],
    }),
  );
  try {
    Prism.tokenize = () => {
      throw new Error('Highlight failure');
    };
    const editor = await edit(view.container);
    await wait();
    assert.match(
      notificationError().textContent,
      /Failed to load syntax highlighting/,
    );
    assert.equal(editor.state.doc.toString(), 'const draft = 1;');
    await act(() => new Promise((resolve) => setTimeout(resolve, 3300)));
    assert.equal(Boolean(notificationError()), false);
    Prism.tokenize = original;
    await act(async () =>
      [...view.container.querySelectorAll('button')]
        .find((b) => b.textContent === 'Retry')
        .click(),
    );
    await wait();
    assert.equal(Boolean(notificationError()), false);
    assert.equal(
      Boolean(view.container.querySelector('[role="alert"]')),
      false,
    );
    assert.ok(
      editor.contentDOM.querySelector('.ov-file-preview__token--keyword'),
    );
  } finally {
    Prism.tokenize = original;
    await view.unmount();
  }
});
