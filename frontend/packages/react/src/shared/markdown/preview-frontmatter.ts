import { isCollection, parseDocument } from 'yaml';

/** Fence a leading YAML metadata document for the Markdown preview only. */
export function fenceMarkdownFrontmatter(source: string): string {
  const opening = /^\uFEFF?---[ \t]*\r?\n/.exec(source);
  if (!opening) return source;
  const rest = source.slice(opening[0].length);
  const closing = /^(?:---|\.\.\.)[ \t]*(?:\r?\n|$)/m.exec(rest);
  if (!closing) return source;

  const yaml = rest.slice(0, closing.index);
  // Check the entire candidate: Markdown rules can also surround prose or links.
  const { contents, errors } = parseDocument(yaml, { prettyErrors: false });
  if (errors.length || !isCollection(contents)) return source;

  let length = 3;
  for (const marker of yaml.matchAll(/~+/g))
    length = Math.max(length, marker[0].length + 1);
  const fence = '~'.repeat(length);
  const body = rest.slice(closing.index + closing[0].length);
  return `${fence}yaml\n${yaml}${fence}\n\n${body}`;
}
