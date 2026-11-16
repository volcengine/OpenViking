import { isCollection, parseDocument } from 'yaml';

/** Read a leading YAML collection and the offset of the Markdown body. */
export function findMarkdownFrontmatter(source: string) {
  const opening = /^\uFEFF?---[ \t]*\r?\n/.exec(source);
  if (!opening) return;
  const yamlStart = opening[0].length;
  const closing = /^(?:---|\.\.\.)[ \t]*(?:\r?\n|$)/m.exec(
    source.slice(yamlStart),
  );
  if (!closing) return;
  const yamlEnd = yamlStart + closing.index;
  const yaml = source.slice(yamlStart, yamlEnd);
  // Markdown rules can also surround prose or links. Validate the whole block.
  const { contents, errors } = parseDocument(yaml, { prettyErrors: false });
  if (errors.length || !isCollection(contents)) return;
  return { yaml, bodyStart: yamlEnd + closing[0].length };
}

/** Fence a leading YAML metadata document for the Markdown preview only. */
export function fenceMarkdownFrontmatter(source: string): string {
  const frontmatter = findMarkdownFrontmatter(source);
  if (!frontmatter) return source;
  const { yaml, bodyStart } = frontmatter;

  let length = 3;
  for (const marker of yaml.matchAll(/~+/g))
    length = Math.max(length, marker[0].length + 1);
  const fence = '~'.repeat(length);
  const body = source.slice(bodyStart);
  return `${fence}yaml\n${yaml}${fence}\n\n${body}`;
}
