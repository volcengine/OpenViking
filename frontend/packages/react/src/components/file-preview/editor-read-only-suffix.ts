import { Decoration, EditorView, WidgetType } from '@codemirror/view';

class EditorSuffix extends WidgetType {
  constructor(private readonly text: string) {
    super();
  }

  toDOM(view: EditorView) {
    const block = view.dom.ownerDocument.createElement('pre');
    block.className = 'ov-file-preview__editor-suffix';
    block.textContent = this.text;
    return block;
  }
}

/** Keep the suffix at the end of the scrollable editor, outside the saved document. */
export function editorReadOnlySuffix(text: string) {
  const block = Decoration.widget({
    widget: new EditorSuffix(text),
    block: true,
    side: 1,
  });
  return EditorView.decorations.compute(['doc'], ({ doc }) =>
    Decoration.set([block.range(doc.length)]),
  );
}
