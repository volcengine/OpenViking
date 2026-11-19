import type { FileTreeEntryRow } from '../../components/file-tree/index.js';

export const MENTION_ROOT = 'viking://';

/** Use native glob for a literal basename search across unloaded directories. */
export function mentionPattern(keyword: string) {
  const parts = keyword
    .split('/')
    .map((part) =>
      part.replace(/[a-z*?[\]{}\\]/gi, (char) =>
        /[a-z]/i.test(char)
          ? `[${char.toLowerCase()}${char.toUpperCase()}]`
          : `[${char}]`,
      ),
    );
  return `**/*${parts.join('/')}*`;
}

/** Keep the ancestors in search results without making inferred paths selectable. */
export function mentionSearchRows(matches: unknown) {
  if (!Array.isArray(matches)) throw new Error('Invalid file search response');
  const rows = new Map<string, FileTreeEntryRow>();
  for (const match of matches) {
    if (typeof match !== 'string' || !match.startsWith(MENTION_ROOT)) continue;
    const segments = match
      .slice(MENTION_ROOT.length)
      .split('/')
      .filter(Boolean);
    if (segments.some((segment) => segment.startsWith('.'))) continue;
    let uri = MENTION_ROOT;
    for (const [depth, name] of segments.entries()) {
      uri += depth ? `/${name}` : name;
      const matched = depth === segments.length - 1;
      const previous = rows.get(uri);
      const directory = !matched || match.endsWith('/');
      rows.set(uri, {
        kind: 'entry',
        id: uri,
        uri,
        name,
        depth,
        type: directory ? 'directory' : 'file',
        selectable: matched || previous?.selectable === true,
        hasChildren: !matched || previous?.hasChildren === true,
      });
    }
  }
  // Stable traversal order; an ancestor's visible rows must be contiguous.
  const children = new Map<string, FileTreeEntryRow[]>();
  for (const row of rows.values()) {
    const parent =
      row.depth === 0
        ? MENTION_ROOT
        : row.uri.slice(0, row.uri.lastIndexOf('/'));
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent)!.push(row);
  }
  const flatten = (parent: string): FileTreeEntryRow[] =>
    (children.get(parent) ?? []).flatMap((row) => [row, ...flatten(row.uri)]);
  return flatten(MENTION_ROOT);
}
