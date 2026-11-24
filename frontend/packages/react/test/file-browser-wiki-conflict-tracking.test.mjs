import assert from 'node:assert/strict';
import test from 'node:test';
import { isolateHistory, redo, undo } from '@codemirror/commands';
import { EditorView } from '@codemirror/view';
import { act, click } from './helpers/preview-native-dom.mjs';
import { waitForEditor } from './helpers/preview-editor-dom.mjs';
import { body, mountWikiFile } from './helpers/wiki-conflict-editor-dom.mjs';

test('a conflict follows edits to its highlighted lines until the block is removed', async () => {
  const { view } = await mountWikiFile();
  try {
    await click(view.container.querySelector('button[aria-label="Edit"]'));
    const editor = await waitForEditor(view.container);
    const code = EditorView.findFromDOM(editor);
    const highlighted = () =>
      [...editor.querySelectorAll('.ov-file-preview__annotation-line')].map(
        (line) => line.textContent,
      );
    const edit = (changes) =>
      act(async () =>
        code.dispatch({ changes, annotations: isolateHistory.of('full') }),
      );

    assert.deepEqual(highlighted(), [body]);
    await edit({ from: 0, insert: 'New introduction\n' });
    assert.deepEqual(highlighted(), [body]);

    const line = code.state.doc.line(2);
    const character = line.from + line.text.indexOf('two') + 1;
    await edit({ from: character, to: character + 1 });
    assert.deepEqual(highlighted(), ['The process requires to approvals.']);

    const startOfAnchor = code.state.doc.line(2).from + body.indexOf('two');
    await edit({
      from: startOfAnchor,
      to: startOfAnchor + 'to approvals'.length,
      insert: 'manager approval',
    });
    await edit({ from: startOfAnchor + 'manager '.length, insert: '\n' });
    const edited = ['The process requires manager ', 'approval.'];
    assert.deepEqual(highlighted(), edited);

    await edit({
      from: startOfAnchor,
      to: startOfAnchor + 'manager \napproval'.length,
    });
    assert.deepEqual(highlighted(), []);
    assert.equal(code.state.doc.line(2).text, 'The process requires .');
    await act(async () => assert.equal(undo(code), true));
    assert.deepEqual(highlighted(), edited);
    await act(async () => assert.equal(redo(code), true));
    assert.deepEqual(highlighted(), []);
  } finally {
    await view.unmount();
  }
});

test('a conflict survives the temporary empty range during IME replacement', async () => {
  const { view } = await mountWikiFile();
  try {
    await click(view.container.querySelector('button[aria-label="Edit"]'));
    const editor = await waitForEditor(view.container);
    const code = EditorView.findFromDOM(editor);
    const highlighted = () =>
      editor.querySelector('.ov-file-preview__annotation-line')?.textContent;
    const composition = (name) =>
      editor.dispatchEvent(
        new window.CompositionEvent(name, { bubbles: true }),
      );
    const from = body.indexOf('two approvals');

    await act(async () => {
      composition('compositionstart');
      code.dispatch({
        changes: { from, to: from + 'two approvals'.length },
        userEvent: 'input.type.compose.start',
      });
    });
    assert.equal(highlighted(), 'The process requires .');

    await act(async () => {
      code.dispatch({
        changes: { from, insert: '两次审批' },
        userEvent: 'input.type.compose',
      });
      composition('compositionend');
      await new Promise((resolve) => setTimeout(resolve, 70));
    });
    assert.equal(highlighted(), 'The process requires 两次审批.');

    await act(async () => {
      composition('compositionstart');
      code.dispatch({
        changes: { from, to: from + '两次审批'.length },
        userEvent: 'input.type.compose.start',
      });
      composition('compositionend');
      await new Promise((resolve) => setTimeout(resolve, 70));
    });
    assert.equal(highlighted(), undefined);
  } finally {
    await view.unmount();
  }
});
