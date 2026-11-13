import assert from 'node:assert/strict';
import test from 'node:test';
import { act, createElement as h, StrictMode } from 'react';
import { EditorView } from '@codemirror/view';
import { undo } from '@codemirror/commands';
import Prism from 'prismjs';
import { FilePreview } from '@openviking/react/components/file-preview';
import { mount } from './helpers/module-dom.mjs';
import { waitForEditor } from './helpers/preview-editor-dom.mjs';

const wait = (ms = 130) =>
  act(() => new Promise((resolve) => setTimeout(resolve, ms)));
const edit = async (root) => {
  await act(async () => root.querySelector('[aria-label="Edit"]').click());
  return EditorView.findFromDOM(await waitForEditor(root));
};

test('Prism highlights edits without resetting history, locale, drafts or save state', async () => {
  const initial = '/* first line\nsecond line */\nconst value = 42;\n';
  const saved = [];
  let fail = true,
    finish;
  let actions;
  const render = (locale = 'en-US') =>
    h(
      StrictMode,
      null,
      h(FilePreview, {
        locale,
        tabs: [
          {
            kind: 'file',
            key: 'code',
            source: { content: '', fileName: 'demo.js' },
            editing: {
              loadContent: async () => initial,
              save: (value) =>
                new Promise((resolve, reject) => {
                  saved.push(value);
                  finish = () =>
                    fail ? reject(new Error('failed')) : resolve();
                }),
            },
            slots: {
              actions: (value) => {
                actions = value;
                return null;
              },
            },
          },
        ],
      }),
    );
  const view = await mount(render());
  try {
    await act(async () => actions.startEditing());
    const content = await waitForEditor(view.container);
    const editor = EditorView.findFromDOM(content);
    await wait();
    assert.match(
      content.querySelector('.ov-file-preview__token--keyword').textContent,
      /const/,
    );
    assert.equal(
      content.querySelectorAll('.ov-file-preview__token--comment').length,
      2,
    );
    await act(async () =>
      editor.dispatch({
        changes: {
          from: editor.state.doc.length,
          insert: 'let next = "hello";',
        },
      }),
    );
    const changed = editor.state.doc.toString();
    await view.render(render('zh-CN'));
    assert.equal(await waitForEditor(view.container), content);
    assert.equal(editor.state.doc.toString(), changed);
    assert.equal(content.getAttribute('aria-label'), '编辑正文');
    await act(async () => undo(editor));
    assert.equal(editor.state.doc.toString(), initial);
    await act(async () =>
      editor.dispatch({ changes: { from: 0, insert: '// draft\n' } }),
    );
    const draft = editor.state.doc.toString();
    let saving;
    await act(async () => {
      saving = actions.saveEditing();
    });
    assert.equal(content.getAttribute('contenteditable'), 'false');
    await act(async () => {
      finish();
      await saving;
    });
    assert.equal(editor.state.doc.toString(), draft);
    assert.equal(content.getAttribute('contenteditable'), 'true');
    assert.match(view.container.textContent, /保存失败/);
    fail = false;
    await act(async () => {
      saving = actions.saveEditing();
    });
    await act(async () => {
      finish();
      await saving;
    });
    assert.deepEqual(saved, [draft, draft]);
    assert.equal(view.container.querySelector('.cm-content'), null);
  } finally {
    await view.unmount();
  }
});

test('lazy grammars reuse Prism, honor format hints, and leave unknown files editable', async () => {
  for (const [source, token] of [
    [
      {
        fileName: 'first.tsx',
        content: 'export const App = () => <p>Hello</p>;',
      },
      'keyword',
    ],
    [{ fileName: 'data.json', content: '{"count": 42}' }, 'property'],
    [{ fileName: 'config.yml', content: 'enabled: true\n' }, 'boolean'],
    [
      {
        fileName: 'note.txt',
        fileType: 'python',
        content: 'def greet():\n    return "hi"',
      },
      'keyword',
    ],
    [
      {
        fileName: 'unknown.custom',
        content: 'plain text <script>literal</script>',
      },
      null,
    ],
  ]) {
    const view = await mount(
      h(FilePreview, {
        tabs: [
          {
            kind: 'file',
            key: 'a',
            source: { ...source, content: '' },
            editing: {
              loadContent: async () => source.content,
              save: async () => {},
            },
          },
        ],
      }),
    );
    try {
      const editor = await edit(view.container);
      await wait(180);
      if (token)
        assert.ok(
          editor.contentDOM.querySelector(`.ov-file-preview__token--${token}`),
          source.fileName,
        );
      else assert.equal(editor.contentDOM.querySelector('script'), null);
      assert.equal(editor.state.doc.toString(), source.content);
      assert.equal(editor.contentDOM.getAttribute('contenteditable'), 'true');
    } finally {
      await view.unmount();
    }
  }
  assert.ok(Prism.languages.tsx);
  assert.ok(Prism.languages.yaml);
  assert.ok(Prism.languages.python);
});
