import { settle } from './module-dom.mjs';
import { act } from 'react';
import { EditorView } from '@codemirror/view';

// CodeMirror measures ranges; jsdom has no layout engine.
globalThis.Window = window.Window;
globalThis.getComputedStyle = window.getComputedStyle.bind(window);
window.Range.prototype.getClientRects = () => [];
window.Range.prototype.getBoundingClientRect = () => ({
  left: 0,
  right: 0,
  top: 0,
  bottom: 0,
  width: 0,
  height: 0,
});

export async function waitForEditor(root = document) {
  for (let tries = 0; tries < 100; tries++) {
    const editor = root.querySelector('.cm-content');
    if (editor) return editor;
    await settle();
  }
  throw new Error('CodeMirror did not load');
}

export async function setEditor(content, value) {
  const view = EditorView.findFromDOM(content);
  await act(async () =>
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: value },
    }),
  );
  await settle();
}
