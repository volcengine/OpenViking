import type { Definition, Nodes, Paragraph } from 'mdast';
import {
  markdownRange,
  walkMarkdown,
  type MarkdownRange,
} from './markdown-source.js';

const eastAsian =
  /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\u3000-\u303f\uff00-\uffef]/u;

interface Edit extends MarkdownRange {
  replacement: string;
}

export function markdownDefinitions(root: Nodes) {
  const definitions = new Map<string, Definition>();
  walkMarkdown(root, (node) => {
    if (node.type === 'definition' && !definitions.has(node.identifier))
      definitions.set(node.identifier, node);
  });
  return definitions;
}

function continuation(source: string, start: number, depth: number) {
  let end = start;
  for (let i = 0; i < depth; i++) {
    const prefix = /^ {0,3}>[ \t]?/.exec(source.slice(end));
    if (!prefix) break;
    end += prefix[0].length;
  }
  return end;
}

function wraps(
  source: string,
  range: MarkdownRange,
  depth: number,
  code: boolean,
) {
  return [...source.slice(range.start, range.end).matchAll(/\r\n|[\r\n]/g)].map(
    (match): Edit => {
      const lineEnd = range.start + match.index;
      const nextLine = continuation(source, lineEnd + match[0].length, depth);
      if (code) return { start: lineEnd, end: nextLine, replacement: ' ' };
      const preceding = source.slice(range.start, lineEnd);
      const start = lineEnd - (/[ \t]+$/.exec(preceding)?.[0].length ?? 0);
      const end =
        nextLine + (/^[ \t]+/.exec(source.slice(nextLine))?.[0].length ?? 0);
      const joined =
        eastAsian.test(source[start - 1] ?? '') &&
        eastAsian.test(source[end] ?? '');
      return { start, end, replacement: joined ? '' : ' ' };
    },
  );
}

export function projectParagraph(
  source: string,
  node: Paragraph,
  depth: number,
  definitions: ReadonlyMap<string, Definition>,
) {
  const range = markdownRange(node);
  if (!range) return;
  const edits: Edit[] = [];
  const markup: Array<MarkdownRange & { identity: unknown[] }> = [];
  let containsHtml = false;
  walkMarkdown(node, (part) => {
    if (part.type === 'html') containsHtml = true;
    const span = markdownRange(part);
    if (!span || part.type === 'paragraph') return;
    if (part.type === 'text' || part.type === 'inlineCode')
      edits.push(...wraps(source, span, depth, part.type === 'inlineCode'));
    if (part.type === 'text') return;
    const link =
      part.type === 'linkReference' || part.type === 'imageReference'
        ? definitions.get(part.identifier)
        : part.type === 'link' || part.type === 'image'
          ? part
          : undefined;
    markup.push({ ...span, identity: [part.type, link?.url, link?.title] });
  });
  if (containsHtml) return;
  const structure = (span: MarkdownRange) =>
    JSON.stringify(
      markup
        .filter(({ start, end }) => span.start <= start && span.end >= end)
        .map(({ identity }) => identity),
    );
  if (!edits.length)
    return {
      value: source.slice(range.start, range.end),
      sourceRange: (start: number, end: number) => ({
        start: start + range.start,
        end: end + range.start,
      }),
      markup,
      structure,
    };

  let value = '';
  const offsets: number[] = [];
  const append = (start: number, end: number) => {
    value += source.slice(start, end);
    for (let index = start; index < end; index++) offsets.push(index);
  };
  let index = range.start;
  for (const edit of edits) {
    append(index, edit.start);
    value += edit.replacement;
    if (edit.replacement) offsets.push(edit.start);
    index = edit.end;
  }
  append(index, range.end);
  return {
    value,
    sourceRange: (start: number, end: number) => ({
      start: offsets[start]!,
      end: offsets[end - 1]! + 1,
    }),
    markup,
    structure,
  };
}
