import type { OvRequestOptions } from '../../client/types.js';
import type { FileTreeRow } from '../../components/file-tree/index.js';
import type { FileBrowserDashboard } from './dashboard-model.js';
import { useWikiConflictSummaries } from './use-wiki-conflict-summaries.js';

export function useWikiConflictIndicators(
  rows: readonly FileTreeRow[],
  dashboards: ReadonlyMap<string, FileBrowserDashboard>,
  current: FileBrowserDashboard | undefined,
  identifyUri: (uri: string) => string,
  generation: number,
  requestOptions?: OvRequestOptions,
) {
  const directories = rows.flatMap((row) => {
    if (row.kind !== 'entry' || row.type !== 'directory') return [];
    const board = dashboards.get(row.uri);
    return board?.kind === 'wiki' && (board.isRoot || row.expanded)
      ? [identifyUri(row.uri)]
      : [];
  });
  if (current?.kind === 'wiki' && current.isRoot)
    directories.push(identifyUri(current.uri));

  const summaries = useWikiConflictSummaries(
    directories,
    generation,
    requestOptions,
  );
  return (dashboard: FileBrowserDashboard) => {
    if (dashboard.kind !== 'wiki') return;
    const uri = identifyUri(dashboard.uri);
    const own = summaries.get(uri);
    const parent = summaries.get(uri.slice(0, uri.lastIndexOf('/')));
    if (own?.conflictFileCount) return true;
    if (own && !own.limited) return false;
    if (
      parent?.directDirectories.includes(uri) ||
      parent?.directFiles.includes(uri)
    )
      return true;
    if (parent && !parent.limited) return false;
    return dashboard.pendingConflicts;
  };
}
