import { isCollection, parseDocument } from 'yaml';

/** Find the boundaries of a leading block delimited like YAML frontmatter. */
export function findMarkdownFrontmatter(source: string) {
  const opening = /^\uFEFF?---[ \t]*\r?\n/.exec(source);
  if (!opening) return;
  const yamlStart = opening[0].length;
  const closing = /^(?:---|\.\.\.)[ \t]*(?:\r?\n|$)/m.exec(
    source.slice(yamlStart),
  );
  if (!closing) return;
  const yamlEnd = yamlStart + closing.index;
  return { yamlStart, yamlEnd, bodyStart: yamlEnd + closing[0].length };
}

/** Fence a leading YAML metadata document for the Markdown preview only. */
export function fenceMarkdownFrontmatter(source: string): string {
  const boundaries = findMarkdownFrontmatter(source);
  if (!boundaries) return source;
  const yaml = source.slice(boundaries.yamlStart, boundaries.yamlEnd);
  // Check the entire candidate: Markdown rules can also surround prose or links.
  const { contents, errors } = parseDocument(yaml, { prettyErrors: false });
  if (errors.length || !isCollection(contents)) return source;

  let length = 3;
  for (const marker of yaml.matchAll(/~+/g))
    length = Math.max(length, marker[0].length + 1);
  const fence = '~'.repeat(length);
  const body = source.slice(boundaries.bodyStart);
  return `${fence}yaml\n${yaml}${fence}\n\n${body}`;
}
