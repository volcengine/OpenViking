import assert from 'node:assert/strict';
import test from 'node:test';
import { EditorView } from '@codemirror/view';
import { act, click } from './helpers/preview-native-dom.mjs';
import { waitForEditor } from './helpers/preview-editor-dom.mjs';
import { mountWikiFile } from './helpers/wiki-conflict-editor-dom.mjs';

test('the conflict tooltip closes when the editor scrolls or loses focus', async () => {
  const { view } = await mountWikiFile();
  const outside = document.createElement('button');
  document.body.append(outside);
  try {
    await click(view.container.querySelector('button[aria-label="Edit"]'));
    const editor = await waitForEditor(view.container);
    const code = EditorView.findFromDOM(editor);
    const highlighted = () =>
      editor.querySelector('.ov-file-preview__annotation-line');
    const tooltip = () =>
      view.container.querySelector('.ov-file-preview__annotation-tooltip');
    const hover = () =>
      act(async () =>
        highlighted().dispatchEvent(
          new window.MouseEvent('mousemove', { bubbles: true }),
        ),
      );
    await act(async () => editor.focus());
    await hover();
    assert.ok(tooltip());

    await act(async () =>
      code.scrollDOM.dispatchEvent(new window.Event('scroll')),
    );
    assert.equal(tooltip(), null);
    assert.ok(highlighted());

    await hover();
    assert.ok(tooltip());
    await act(async () =>
      editor
        .closest('.ov-file-preview__editor')
        .dispatchEvent(new window.Event('scroll')),
    );
    assert.equal(tooltip(), null);
    assert.ok(highlighted());

    await hover();
    assert.ok(tooltip());
    await act(async () => outside.focus());
    assert.equal(document.activeElement, outside);
    assert.equal(tooltip(), null);
    assert.ok(highlighted());
  } finally {
    outside.remove();
    await view.unmount();
  }
});
