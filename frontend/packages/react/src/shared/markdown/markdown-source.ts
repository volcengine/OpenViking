import { fromMarkdown } from 'mdast-util-from-markdown';
import type { Nodes, Parent } from 'mdast';
import { findMarkdownFrontmatter } from './preview-frontmatter.js';

export interface MarkdownRange {
  start: number;
  end: number;
}

export function markdownRange(node: Nodes): MarkdownRange | undefined {
  const start = node.position?.start.offset;
  const end = node.position?.end.offset;
  if (start !== undefined && end !== undefined) return { start, end };
}

export function walkMarkdown(
  node: Nodes,
  visit: (node: Nodes, quoteDepth: number) => void,
  quoteDepth = 0,
) {
  visit(node, quoteDepth);
  if ('children' in node) {
    const depth = quoteDepth + Number(node.type === 'blockquote');
    for (const child of (node as Parent).children)
      walkMarkdown(child, visit, depth);
  }
}

export function readMarkdownSource(document: string) {
  const bodyStart = findMarkdownFrontmatter(document)?.bodyStart ?? 0;
  const source = document.slice(bodyStart);
  return { bodyStart, source, root: fromMarkdown(source) };
}

export function markdownComments(source: string, root: Nodes) {
  const ranges: MarkdownRange[] = [];
  walkMarkdown(root, (node) => {
    if (node.type !== 'html') return;
    const range = markdownRange(node);
    if (!range) return;
    const raw = source.slice(range.start, range.end);
    const comments =
      /<(script|pre|style|textarea)(?:\s[^>]*|\s*)>[\s\S]*?(?:<\/\1\s*>|$)|(<!--[\s\S]*?(?:-->|$))|<\/?[a-z][\w:-]*(?:[^<>"']|"[^"]*"|'[^']*')*>/gi;
    for (const match of raw.matchAll(comments)) {
      if (!match[2]) continue;
      const start = range.start + match.index;
      ranges.push({ start, end: start + match[0].length });
    }
  });
  return ranges;
}
