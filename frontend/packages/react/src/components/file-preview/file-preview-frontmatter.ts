/** Fence a leading YAML metadata document for the Markdown preview only. */
export function fenceMarkdownFrontmatter(source: string): string {
  const opening = /^\uFEFF?---[ \t]*\r?\n/.exec(source);
  if (!opening) return source;
  const rest = source.slice(opening[0].length);
  const closing = /^(?:---|\.\.\.)[ \t]*(?:\r?\n|$)/m.exec(rest);
  if (!closing) return source;

  const yaml = rest.slice(0, closing.index);
  const first = yaml.split(/\r?\n/).find((line) => !/^\s*(?:#|$)/.test(line));
  // Ordinary Markdown rules around prose are not a metadata document.
  if (
    !first ||
    !/^(?:[ \t]*[^:#]+:[ \t]|[ \t]*[^:#]+:$|[ \t]*-[ \t])/.test(first)
  )
    return source;

  let length = 3;
  for (const marker of yaml.matchAll(/~+/g))
    length = Math.max(length, marker[0].length + 1);
  const fence = '~'.repeat(length);
  const body = rest.slice(closing.index + closing[0].length);
  return `${fence}yaml\n${yaml}${fence}\n\n${body}`;
}
