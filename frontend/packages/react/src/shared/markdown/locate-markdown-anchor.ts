import { findMarkdownFrontmatter } from './preview-frontmatter.js';

type MarkdownAnchorLocation =
  | { status: 'missing' | 'ambiguous' }
  | { status: 'unique'; start: number; end: number; line: number };

/** Prepare one document for anchor searches while preserving source offsets. */
export function createMarkdownAnchorLocator(document: string) {
  const bodyStart = findMarkdownFrontmatter(document)?.bodyStart ?? 0;
  const body = document
    .slice(bodyStart)
    .replace(/<!--[\s\S]*?(?:-->|$)/g, (value) =>
      value.replace(/[^\r\n]/g, ' '),
    );
  return (matchText: string): MarkdownAnchorLocation => {
    const offset = matchText ? body.indexOf(matchText) : -1;
    if (offset < 0) return { status: 'missing' };
    if (body.indexOf(matchText, offset + 1) >= 0)
      return { status: 'ambiguous' };
    const start = bodyStart + offset;
    return {
      status: 'unique',
      start,
      end: start + matchText.length,
      line: document.slice(0, start).split('\n').length,
    };
  };
}

export function locateMarkdownAnchor(document: string, matchText: string) {
  return createMarkdownAnchorLocator(document)(matchText);
}
