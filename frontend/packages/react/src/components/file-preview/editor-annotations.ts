import {
  RangeSet,
  StateEffect,
  StateField,
  type Text,
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
import { createMarkdownAnchorLocator } from '../../shared/markdown/locate-markdown-anchor.js';
import type { FilePreviewEditorAnnotation } from './types.js';

const lineClass = 'ov-file-preview__annotation-line';
const gutterClass = 'ov-file-preview__annotation-gutter';
const tooltipClass = 'ov-file-preview__annotation-tooltip';
const highlightedLine = Decoration.line({ class: lineClass });
const highlightedGutter = new (class extends GutterMarker {
  elementClass = gutterClass;
})();
const showAnnotationAt = StateEffect.define<number | null>();

function findAnnotatedLines(
  doc: Text,
  annotations: readonly FilePreviewEditorAnnotation[],
) {
  const locate = createMarkdownAnchorLocator(doc.toString());
  const matches = new Map<number, FilePreviewEditorAnnotation[]>();
  for (const annotation of annotations) {
    const location = locate(annotation.matchText.replace(/\r\n?/g, '\n'));
    if (location.status !== 'unique') continue;
    const start = doc.lineAt(location.start).number;
    const end = doc.lineAt(location.end - 1).number;
    for (let number = start; number <= end; number += 1) {
      const { from } = doc.line(number);
      const line = matches.get(from);
      if (line) line.push(annotation);
      else matches.set(from, [annotation]);
    }
  }
  return matches;
}

function annotationTooltip(
  pos: number,
  annotations: readonly FilePreviewEditorAnnotation[],
): Tooltip {
  return {
    pos,
    above: true,
    arrow: true,
    create(view) {
      const doc = view.dom.ownerDocument;
      const dom = doc.createElement('div');
      dom.className = tooltipClass;
      dom.setAttribute('role', 'tooltip');
      const content = doc.createElement('div');
      content.className = 'ov-file-preview__annotation-tooltip-content';
      for (const annotation of annotations) {
        const section = doc.createElement('section');
        const description = doc.createElement('strong');
        description.textContent = annotation.description;
        section.append(description);
        if (annotation.reason) {
          const reason = doc.createElement('p');
          reason.textContent = annotation.reason;
          section.append(reason);
        }
        content.append(section);
      }
      dom.append(content);
      return { dom };
    },
  };
}

export function editorAnnotations(
  annotations: readonly FilePreviewEditorAnnotation[],
) {
  const rows = StateField.define({
    create: ({ doc }) => findAnnotatedLines(doc, annotations),
    update(value, transaction) {
      return transaction.docChanged
        ? findAnnotatedLines(transaction.newDoc, annotations)
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
        if (event.key === 'Escape') this.show(null);
      };

      private show(pos: number | null) {
        this.cancelHide();
        if ((this.view.state.field(hovered)?.pos ?? null) !== pos)
          this.view.dispatch({ effects: showAnnotationAt.of(pos) });
      }

      private hideSoon = () => {
        if (this.hideTimer !== undefined || !this.view.state.field(hovered))
          return;
        // Give the pointer time to cross the gap between the row and its tooltip.
        this.hideTimer = setTimeout(() => this.show(null), 120);
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
      }
    },
  );

  return [
    rows,
    hovered,
    hover,
    tooltips({
      position: 'absolute',
      tooltipSpace: (view) => view.dom.getBoundingClientRect(),
    }),
  ];
}
