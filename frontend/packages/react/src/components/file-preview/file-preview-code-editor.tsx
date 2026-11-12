import { useEffect, useRef, useState } from 'react';
import { Compartment, EditorState, Transaction } from '@codemirror/state';
import { EditorView, keymap, lineNumbers } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { Button } from '../../shared/primitives/button/button.js';
import { loadPrismLanguage, prismLanguage } from './prism-language.js';
import { prismHighlight } from './prism-highlight.js';
import type { FilePreviewLabels, FilePreviewSource } from './types.js';

export interface FilePreviewCodeEditorProps {
  draft: string;
  source: FilePreviewSource;
  saving: boolean;
  text: Required<FilePreviewLabels>;
  onChange: (value: string) => void;
}

export function FilePreviewCodeEditor(props: FilePreviewCodeEditorProps) {
  const container = useRef<HTMLDivElement>(null);
  const editor = useRef<EditorView | null>(null);
  const latest = useRef(props);
  latest.current = props;
  const access = useRef(new Compartment());
  const highlighting = useRef(new Compartment());
  const [highlightFailed, setHighlightFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const language = prismLanguage(props.source);

  useEffect(() => {
    const view = new EditorView({
      parent: container.current!,
      state: EditorState.create({
        doc: latest.current.draft,
        extensions: [
          lineNumbers(),
          history(),
          EditorView.lineWrapping,
          keymap.of([...defaultKeymap, ...historyKeymap]),
          access.current.of([]),
          highlighting.current.of([]),
          EditorView.updateListener.of((update) => {
            if (
              update.docChanged &&
              update.state.doc.toString() !== latest.current.draft
            )
              latest.current.onChange(update.state.doc.toString());
          }),
        ],
      }),
    });
    editor.current = view;
    return () => {
      editor.current = null;
      view.destroy();
    };
  }, []);

  useEffect(() => {
    const view = editor.current!;
    view.dispatch({
      effects: access.current.reconfigure([
        EditorState.readOnly.of(props.saving),
        EditorView.editable.of(!props.saving),
        EditorView.contentAttributes.of({
          'aria-label': props.text.editor,
          'aria-readonly': String(props.saving),
          tabindex: '0',
          spellcheck: 'false',
        }),
      ]),
    });
  }, [props.saving, props.text.editor]);

  useEffect(() => {
    const view = editor.current!;
    if (!view.state.toText(props.draft).eq(view.state.doc))
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: props.draft },
        annotations: Transaction.addToHistory.of(false),
      });
  }, [props.draft]);

  useEffect(() => {
    const view = editor.current!;
    let active = true;
    setHighlightFailed(false);
    view.dispatch({ effects: highlighting.current.reconfigure([]) });
    void loadPrismLanguage(language)
      .then((grammar) => {
        if (active && grammar)
          view.dispatch({
            effects: highlighting.current.reconfigure(
              prismHighlight(grammar, () => {
                if (active) setHighlightFailed(true);
              }),
            ),
          });
      })
      .catch(() => {
        if (active) setHighlightFailed(true);
      });
    return () => {
      active = false;
    };
  }, [language, attempt]);

  return (
    <>
      <div className="ov-file-preview__code-editor" ref={container} />
      {highlightFailed ? (
        <div className="ov-file-preview__notice" role="alert">
          {props.text.highlightFailed}{' '}
          <Button size="small" onClick={() => setAttempt((value) => value + 1)}>
            {props.text.retry}
          </Button>
        </div>
      ) : null}
    </>
  );
}
