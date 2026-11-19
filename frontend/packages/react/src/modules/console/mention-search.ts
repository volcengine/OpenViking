import type { FileTreeEntryRow } from '../../components/file-tree/index.js';

export const MENTION_ROOT = 'viking://';
const compileSkillRoots = [
  'viking://system',
  'viking://~/skills',
  'viking://agent/skills',
];

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

/** A compile Skill search returns its entry file, matched by filename or directory. */
function compileSkillPatterns(keyword: string) {
  const pattern = mentionPattern(keyword);
  const segments = keyword.split('/');
  const filename = segments.pop()!.toLowerCase();
  if (!'skill.md'.includes(filename)) return [`${pattern}/SKILL.md`];
  if (segments.length === 0) return ['**/SKILL.md'];
  return [
    `${pattern}/SKILL.md`,
    `${mentionPattern(segments.join('/'))}/SKILL.md`,
  ];
}

export function compileSkillQueries(query: string) {
  const queriesForRoot = (uri: string, relativePath: string) =>
    compileSkillPatterns(relativePath).map((pattern) => ({
      uri,
      pattern,
      node_limit: 256,
    }));
  if (!query.startsWith(MENTION_ROOT)) {
    return compileSkillRoots.flatMap((uri) => queriesForRoot(uri, query));
  }
  const root =
    compileSkillRoots.find(
      (uri) => query === uri || query.startsWith(`${uri}/`),
    ) ?? /^viking:\/\/user\/[^/]+\/skills(?=\/|$)/.exec(query)?.[0];
  return root ? queriesForRoot(root, query.slice(root.length + 1)) : [];
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
