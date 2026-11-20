import {
  RangeSet,
  StateEffect,
  StateField,
  type EditorState,
  type Extension,
} from '@codemirror/state';
import {
  Decoration,
  EditorView,
  GutterMarker,
  ViewPlugin,
  gutterLineClass,
  showTooltip,
  tooltips,
  type Tooltip,
} from '@codemirror/view';
import {
  annotatedLines,
  trackAnnotationRanges,
} from './editor-annotation-ranges.js';
import {
  annotationTooltip,
  tooltipClass,
} from './editor-annotation-tooltip.js';
import type { FilePreviewEditorAnnotation } from './types.js';

const lineClass = 'ov-file-preview__annotation-line';
const gutterClass = 'ov-file-preview__annotation-gutter';
const highlightedLine = Decoration.line({ class: lineClass });
const highlightedGutter = new (class extends GutterMarker {
  elementClass = gutterClass;
})();
const showAnnotationAt = StateEffect.define<number | null>();

export function editorAnnotations(
  annotations: readonly FilePreviewEditorAnnotation[],
): {
  extensions: Extension[];
  firstLine: (state: EditorState) => number | undefined;
  annotationPosition: (
    state: EditorState,
    matchText: string,
  ) => number | undefined;
} {
  const tracked = trackAnnotationRanges(annotations);
  const rows = StateField.define({
    create: (state) => annotatedLines(state.doc, state.field(tracked.field)),
    update(value, transaction) {
      const ranges = transaction.state.field(tracked.field);
      return transaction.docChanged ||
        ranges !== transaction.startState.field(tracked.field, false)
        ? annotatedLines(transaction.newDoc, ranges)
        : value;
    },
    provide: (field) => [
      EditorView.decorations.from(field, (value) =>
        Decoration.set(
          [...value.keys()].map((pos) => highlightedLine.range(pos)),
          true,
        ),
      ),
      gutterLineClass.from(field, (value) =>
        RangeSet.of(
          [...value.keys()].map((pos) => highlightedGutter.range(pos)),
          true,
        ),
      ),
    ],
  });
  const hovered = StateField.define<Tooltip | null>({
    create: () => null,
    update(value, transaction) {
      if (transaction.docChanged) return null;
      for (const effect of transaction.effects) {
        if (!effect.is(showAnnotationAt)) continue;
        const pos = effect.value;
        if (pos === value?.pos) return value;
        const items =
          pos === null ? undefined : transaction.state.field(rows).get(pos);
        return pos !== null && items ? annotationTooltip(pos, items) : null;
      }
      return value;
    },
    provide: (field) => showTooltip.from(field),
  });

  const hover = ViewPlugin.fromClass(
    class {
      private hideTimer?: ReturnType<typeof setTimeout>;

      constructor(readonly view: EditorView) {
        // Listen on the editor root so the gutter and tooltip also receive hover.
        view.dom.addEventListener('mousemove', this.onMove);
        view.dom.addEventListener('mouseleave', this.hideSoon);
        view.dom.addEventListener('mousedown', this.onDown);
        view.dom.addEventListener('keydown', this.onKey);
        view.dom.addEventListener('focusout', this.onFocusOut);
      }

      private onMove = (event: MouseEvent) => {
        const target = event.target;
        if (!(target instanceof Element)) return;
        if (target.closest(`.${tooltipClass}`)) {
          this.cancelHide();
          return;
        }
        const { view } = this;
        const line = target.closest(`.${lineClass}`);
        if (line) {
          this.show(view.state.doc.lineAt(view.posAtDOM(line)).from);
        } else if (target.closest(`.${gutterClass}`)) {
          this.show(
            view.lineBlockAtHeight(event.clientY - view.documentTop).from,
          );
        } else {
          this.hideSoon();
        }
      };

      private onDown = (event: MouseEvent) => {
        if (!(
          event.target instanceof Element &&
          event.target.closest(`.${tooltipClass}`)
        ))
          this.show(null);
      };

      private onKey = (event: KeyboardEvent) => {
        if (event.key === 'Escape') this.hide();
      };

      private onFocusOut = ({ relatedTarget }: FocusEvent) => {
        if (
          !(relatedTarget instanceof Node) ||
          !this.view.dom.contains(relatedTarget)
        )
          this.hide();
      };

      hide = () => this.show(null);

      private show(pos: number | null) {
        this.cancelHide();
        if ((this.view.state.field(hovered)?.pos ?? null) !== pos)
          this.view.dispatch({ effects: showAnnotationAt.of(pos) });
      }

      private hideSoon = () => {
        if (this.hideTimer !== undefined || !this.view.state.field(hovered))
          return;
        // Give the pointer time to cross the gap between the row and its tooltip.
        this.hideTimer = setTimeout(this.hide, 120);
      };

      private cancelHide() {
        clearTimeout(this.hideTimer);
        this.hideTimer = undefined;
      }

      destroy() {
        this.cancelHide();
        const { dom } = this.view;
        dom.removeEventListener('mousemove', this.onMove);
        dom.removeEventListener('mouseleave', this.hideSoon);
        dom.removeEventListener('mousedown', this.onDown);
        dom.removeEventListener('keydown', this.onKey);
        dom.removeEventListener('focusout', this.onFocusOut);
      }
    },
    {
      eventObservers: {
        scroll(event) {
          // CodeMirror also observes scrolling in the editor's parents.
          if (event.type === 'scroll') this.hide();
        },
      },
    },
  );

  return {
    extensions: [
      ...tracked.extensions,
      rows,
      hovered,
      hover,
      tooltips({
        position: 'absolute',
        tooltipSpace: (view) => view.dom.getBoundingClientRect(),
      }),
    ],
    annotationPosition(state, matchText) {
      return state
        .field(tracked.field)
        .find(({ annotation }) => annotation.matchText === matchText)?.from;
    },
    firstLine(state) {
      let first: number | undefined;
      for (const pos of state.field(rows).keys())
        if (first === undefined || pos < first) first = pos;
      return first;
    },
  };
}
