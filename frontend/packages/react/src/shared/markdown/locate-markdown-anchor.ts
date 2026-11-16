import { findMarkdownFrontmatter } from './preview-frontmatter.js';

/** Keep source offsets while excluding frontmatter and comments from anchors. */
export function locateMarkdownAnchor(
  document: string,
  matchText: string,
):
  | { status: 'missing' | 'ambiguous' }
  | { status: 'unique'; start: number; end: number; line: number } {
  const bodyStart = findMarkdownFrontmatter(document)?.bodyStart ?? 0;
  const body = document
    .slice(bodyStart)
    .replace(/<!--[\s\S]*?(?:-->|$)/g, (value) =>
      value.replace(/[^\r\n]/g, ' '),
    );
  const offset = matchText ? body.indexOf(matchText) : -1;
  if (offset < 0) return { status: 'missing' };
  if (body.indexOf(matchText, offset + 1) >= 0) return { status: 'ambiguous' };
  const start = bodyStart + offset;
  return {
    status: 'unique',
    start,
    end: start + matchText.length,
    line: document.slice(0, start).split('\n').length,
  };
}
