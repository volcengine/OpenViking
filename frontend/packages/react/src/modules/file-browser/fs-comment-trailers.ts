/** YAML values and code examples remain document content, including literal markers. */
export function isQuotedComment(content: string, offset: number): boolean {
  if (/^(?: {4}| {0,3}\t)/.test(linePrefix(content, offset))) return true;
  const head =
    /^(?:\uFEFF)?---(?:\r\n|\r|\n)[\s\S]*?(?:\r\n|\r|\n)(?:---|\.\.\.)(?:\r\n|\r|\n|$)/.exec(
      content,
    )?.[0];
  if (head && offset < head.length) return true;
  let fence: string | undefined;
  for (const line of content
    .slice(head?.length ?? 0, offset)
    .split(/\r\n|\r|\n/)) {
    const match = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
    if (!match) continue;
    const marker = match[1] ?? '';
    const suffix = match[2] ?? '';
    if (!fence) {
      if (marker[0] !== '`' || !suffix.includes('`')) fence = marker;
    } else if (
      marker[0] === fence[0] &&
      marker.length >= fence.length &&
      !suffix.trim()
    ) {
      fence = undefined;
    }
  }
  return fence !== undefined;
}

/** An incomplete trailer must start on its own line, never inside a string or prose. */
export function startsCommentLine(content: string, offset: number): boolean {
  return !linePrefix(content, offset).trim();
}

function linePrefix(content: string, offset: number): string {
  const start =
    Math.max(
      content.lastIndexOf('\n', offset - 1),
      content.lastIndexOf('\r', offset - 1),
    ) + 1;
  return content.slice(start, offset);
}
