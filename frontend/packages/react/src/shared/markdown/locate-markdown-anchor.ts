import { fromMarkdown } from 'mdast-util-from-markdown';
import {
  markdownComments,
  markdownRange,
  readMarkdownSource,
  walkMarkdown,
  type MarkdownRange,
} from './markdown-source.js';
import { markdownDefinitions, projectParagraph } from './markdown-soft-wrap.js';

type MarkdownAnchorLocation =
  | { status: 'missing' | 'ambiguous' }
  | { status: 'unique'; start: number; end: number; line: number };

function* occurrences(source: string, text: string) {
  for (
    let position = source.indexOf(text);
    position >= 0;
    position = source.indexOf(text, position + 1)
  )
    yield position;
}

const overlaps = (left: MarkdownRange, right: MarkdownRange) =>
  left.start < right.end && right.start < left.end;

const contains = (outer: MarkdownRange, inner: MarkdownRange) =>
  outer.start <= inner.start && outer.end >= inner.end;

/** Prepare one document for anchor searches while preserving source offsets. */
export function createMarkdownAnchorLocator(document: string) {
  const { bodyStart, source, root } = readMarkdownSource(document);
  const definitions = markdownDefinitions(root);
  const ignored = markdownComments(source, root);
  walkMarkdown(root, (node) => {
    if (node.type !== 'definition') return;
    const range = markdownRange(node);
    if (range) ignored.push(range);
  });
  const definitionSource = [...definitions.values()]
    .flatMap((definition) => {
      const range = markdownRange(definition);
      return range ? [source.slice(range.start, range.end)] : [];
    })
    .join('\n\n');
  let paragraphs:
    NonNullable<ReturnType<typeof projectParagraph>>[] | undefined;
  const location = (span: MarkdownRange): MarkdownAnchorLocation => ({
    status: 'unique',
    start: bodyStart + span.start,
    end: bodyStart + span.end,
    line: document.slice(0, bodyStart + span.start).split(/\r\n|[\r\n]/).length,
  });

  return (matchText: string): MarkdownAnchorLocation => {
    if (!matchText) return { status: 'missing' };
    let exact: MarkdownRange | undefined;
    for (const start of occurrences(source, matchText)) {
      const span = { start, end: start + matchText.length };
      if (ignored.some((range) => overlaps(span, range))) continue;
      if (exact) return { status: 'ambiguous' };
      exact = span;
    }
    if (exact) return location(exact);

    const anchor = fromMarkdown(`${matchText}\n\n${definitionSource}`);
    const [first, ...rest] = anchor.children;
    if (
      first?.type !== 'paragraph' ||
      rest.some((node) => node.type !== 'definition')
    )
      return { status: 'missing' };
    const anchorText = projectParagraph(
      matchText,
      first,
      0,
      markdownDefinitions(anchor),
    );
    if (!anchorText?.value) return { status: 'missing' };
    const structure = anchorText.structure({
      start: 0,
      end: matchText.length,
    });
    if (!paragraphs) {
      paragraphs = [];
      walkMarkdown(root, (node, depth) => {
        if (node.type !== 'paragraph') return;
        const paragraph = projectParagraph(source, node, depth, definitions);
        if (paragraph) paragraphs!.push(paragraph);
      });
    }

    let match: MarkdownRange | undefined;
    for (const paragraph of paragraphs) {
      for (const start of occurrences(paragraph.value, anchorText.value)) {
        const span = paragraph.sourceRange(
          start,
          start + anchorText.value.length,
        );
        if (
          paragraph.markup.some(
            (range) => overlaps(span, range) && !contains(span, range),
          )
        )
          continue;
        if (paragraph.structure(span) !== structure) continue;
        if (match) return { status: 'ambiguous' };
        match = span;
      }
    }
    return match ? location(match) : { status: 'missing' };
  };
}

export function locateMarkdownAnchor(document: string, matchText: string) {
  return createMarkdownAnchorLocator(document)(matchText);
}
