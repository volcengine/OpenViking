import { invertedEffects } from '@codemirror/commands';
import { StateEffect, StateField } from '@codemirror/state';
import type { ChangeDesc, Text } from '@codemirror/state';
import { ViewPlugin } from '@codemirror/view';
import { createMarkdownAnchorLocator } from '../../shared/markdown/locate-markdown-anchor.js';
import type { FilePreviewEditorAnnotation } from './types.js';

interface AnnotationRange {
  from: number;
  to: number;
  annotation: FilePreviewEditorAnnotation;
}

function locateRanges(
  doc: Text,
  annotations: readonly FilePreviewEditorAnnotation[],
) {
  const locate = createMarkdownAnchorLocator(doc.toString());
  const ranges: AnnotationRange[] = [];
  for (const annotation of annotations) {
    const location = locate(annotation.matchText.replace(/\r\n?/g, '\n'));
    if (location.status !== 'unique') continue;
    ranges.push({
      from: location.start,
      to: location.end,
      annotation,
    });
  }
  return ranges;
}

function mapRanges(
  ranges: readonly AnnotationRange[],
  changes: ChangeDesc,
  composing = false,
) {
  const mapped: AnnotationRange[] = [];
  for (const range of ranges) {
    // An IME may temporarily delete the selection before inserting replacement text.
    const collapsed = range.from === range.to;
    const from = changes.mapPos(range.from, collapsed ? -1 : 1);
    const to = changes.mapPos(range.to, collapsed ? 1 : -1);
    if (from < to || (composing && from === to))
      mapped.push({ ...range, from, to });
  }
  return mapped;
}

function replacesDocument(changes: ChangeDesc) {
  let replaces = false;
  changes.iterChangedRanges((from, to) => {
    if (from === 0 && to === changes.length) replaces = true;
  });
  return replaces;
}

export function trackAnnotationRanges(
  annotations: readonly FilePreviewEditorAnnotation[],
) {
  const restore = StateEffect.define<readonly AnnotationRange[]>({
    map: mapRanges,
  });
  const setComposing = StateEffect.define<boolean>();
  const composition = StateField.define({
    create: () => false,
    update(value, transaction) {
      for (const effect of transaction.effects)
        if (effect.is(setComposing)) value = effect.value;
      return value;
    },
  });
  const compositionEvents = ViewPlugin.define((view) => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const start = () => {
      clearTimeout(timer);
      view.dispatch({ effects: setComposing.of(true) });
    };
    const end = () => {
      clearTimeout(timer);
      // Let CodeMirror apply pending DOM changes after compositionend.
      timer = setTimeout(
        () => view.dispatch({ effects: setComposing.of(false) }),
        50,
      );
    };
    // Capture compositionstart before CodeMirror deletes a multi-line selection.
    view.dom.addEventListener('compositionstart', start, true);
    view.dom.addEventListener('compositionend', end, true);
    return {
      destroy() {
        clearTimeout(timer);
        view.dom.removeEventListener('compositionstart', start, true);
        view.dom.removeEventListener('compositionend', end, true);
      },
    };
  });
  const field = StateField.define<readonly AnnotationRange[]>({
    create: ({ doc }) => locateRanges(doc, annotations),
    update(ranges, transaction) {
      // Grouped undo events restore the oldest snapshot last.
      for (let index = transaction.effects.length - 1; index >= 0; index -= 1) {
        const effect = transaction.effects[index];
        if (effect?.is(restore)) return effect.value;
      }
      const composing = transaction.state.field(composition);
      if (!transaction.docChanged)
        return !composing && transaction.startState.field(composition, false)
          ? ranges.filter(({ from, to }) => from < to)
          : ranges;
      return !composing && replacesDocument(transaction.changes)
        ? locateRanges(transaction.newDoc, annotations)
        : mapRanges(ranges, transaction.changes, composing);
    },
  });
  const history = invertedEffects.of((transaction) =>
    transaction.docChanged
      ? [restore.of(transaction.startState.field(field))]
      : [],
  );
  return {
    field,
    extensions: [composition, compositionEvents, field, history],
  };
}

export function annotatedLines(doc: Text, ranges: readonly AnnotationRange[]) {
  const lines = new Map<number, FilePreviewEditorAnnotation[]>();
  for (const { from, to, annotation } of ranges) {
    const start = doc.lineAt(from).number;
    const end = doc.lineAt(Math.max(from, to - 1)).number;
    for (let number = start; number <= end; number += 1) {
      const pos = doc.line(number).from;
      const items = lines.get(pos);
      if (items) items.push(annotation);
      else lines.set(pos, [annotation]);
    }
  }
  return lines;
}
