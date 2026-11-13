import { splitVikingUri } from './file-tree-info.js';
import type { FileTreeRow } from './types.js';

const contextRanks = new Map([
  ['user', 0],
  ['users', 0],
  ['session', 1],
  ['sessions', 1],
  ['agent', 2],
  ['agents', 2],
  ['resource', 3],
  ['resources', 3],
]);

/** Sort directory branches as units; keep root files and state rows in their slots. */
export function orderContextRoots(
  rows: readonly FileTreeRow[],
): readonly FileTreeRow[] {
  const branches: { rows: FileTreeRow[]; rank: number }[] = [];

  for (const row of rows) {
    const previous = branches.at(-1);
    if (row.depth > 0 && previous) {
      previous.rows.push(row);
      continue;
    }
    let rank = -1;
    if (row.depth === 0 && row.kind === 'entry' && row.type === 'directory') {
      const [namespace] = splitVikingUri(row.uri);
      rank = contextRanks.get(namespace ?? '') ?? Number.MAX_SAFE_INTEGER;
    }
    branches.push({ rows: [row], rank });
  }

  const directories = branches.filter(({ rank }) => rank >= 0);
  if (directories.length < 2) return rows;
  directories.sort((left, right) => left.rank - right.rank);
  let next = 0;
  return branches.flatMap(({ rows, rank }) =>
    rank < 0 ? rows : (directories[next++]?.rows ?? rows),
  );
}
