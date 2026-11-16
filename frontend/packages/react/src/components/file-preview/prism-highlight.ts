import { StateEffect, type Range } from '@codemirror/state';
import {
  Decoration,
  ViewPlugin,
  type DecorationSet,
  type EditorView,
  type ViewUpdate,
} from '@codemirror/view';
import { Prism } from '../../shared/prism-language.js';

const highlighted = StateEffect.define<DecorationSet>();

function decorations(text: string, grammar: Prism.Grammar) {
  const ranges: Range<Decoration>[] = [];
  let offset = 0;
  const visit = (token: string | Prism.Token, classes: string[]) => {
    if (typeof token === 'string') {
      const end = offset + token.length;
      if (end > offset && classes.length)
        ranges.push(
          Decoration.mark({ class: classes.join(' ') }).range(offset, end),
        );
      offset = end;
      return;
    }
    const names = [token.type, ...[token.alias ?? []].flat()];
    const next = [
      ...classes,
      ...names.map((name) => `ov-file-preview__token--${name}`),
    ];
    for (const child of [token.content].flat()) visit(child, next);
  };
  for (const token of Prism.tokenize(text, grammar)) visit(token, []);
  return Decoration.set(ranges);
}

/** Defer full-document Prism tokenization; map existing marks while typing. */
export function prismHighlight(
  grammar: Prism.Grammar,
  onError: (reason: unknown) => void,
) {
  return ViewPlugin.fromClass(
    class {
      decorations: DecorationSet = Decoration.none;
      timer?: ReturnType<typeof setTimeout>;
      constructor(readonly view: EditorView) {
        this.schedule();
      }
      schedule() {
        clearTimeout(this.timer);
        this.timer = setTimeout(() => {
          try {
            const result = decorations(this.view.state.doc.toString(), grammar);
            this.view.dispatch({ effects: highlighted.of(result) });
          } catch (reason) {
            onError(reason);
          }
        }, 100);
      }
      update(update: ViewUpdate) {
        if (update.docChanged) {
          this.decorations = this.decorations.map(update.changes);
          this.schedule();
        }
        for (const transaction of update.transactions)
          for (const effect of transaction.effects)
            if (effect.is(highlighted)) this.decorations = effect.value;
      }
      destroy() {
        clearTimeout(this.timer);
      }
    },
    { decorations: (plugin) => plugin.decorations },
  );
}
