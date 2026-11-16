/** Keep source offsets while excluding frontmatter and comments from anchors. */
export function locateMarkdownAnchor(
  document: string,
  matchText: string,
):
  | { status: 'missing' | 'ambiguous' }
  | { status: 'unique'; start: number; end: number; line: number } {
  const mask = (value: string) => value.replace(/[^\r\n]/g, ' ');
  const body = document
    .replace(/^(?:\uFEFF)?---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, mask)
    .replace(/<!--[\s\S]*?(?:-->|$)/g, mask);
  const start = matchText ? body.indexOf(matchText) : -1;
  if (start < 0) return { status: 'missing' };
  if (body.indexOf(matchText, start + 1) >= 0) return { status: 'ambiguous' };
  return {
    status: 'unique',
    start,
    end: start + matchText.length,
    line: document.slice(0, start).split('\n').length,
  };
}
