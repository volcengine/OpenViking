import { useState } from 'react';
import type { OvRequestOptions } from '../../client/types.js';
import type {
  FileTreeEntryRow,
  FileTreeRow,
} from '../../components/file-tree/index.js';
import { within, type FileBrowserDashboard } from './dashboard-model.js';
import { useWikiConflictSummaries } from './use-wiki-conflict-summaries.js';
import type { WikiConflictSummary } from './wiki-conflict-summary.js';

export function useWikiConflictIndicators({
  rows,
  knownRows,
  dashboardRows,
  current,
  targets,
  rootUri,
  identifyUri,
  generation,
  requestOptions,
  enabled,
}: {
  rows: readonly FileTreeRow[];
  knownRows: readonly FileTreeRow[];
  dashboardRows: ReadonlyMap<string, FileBrowserDashboard>;
  current: FileBrowserDashboard | undefined;
  targets: readonly { to: string }[];
  rootUri: string;
  identifyUri: (uri: string) => string;
  generation: number;
  requestOptions?: OvRequestOptions;
  enabled: boolean;
}) {
  const directories = rows.flatMap((row) => {
    if (row.kind !== 'entry' || row.type !== 'directory') return [];
    const board = dashboardRows.get(row.uri);
    return board?.kind === 'wiki' && (board.isRoot || row.expanded)
      ? [identifyUri(row.uri)]
      : [];
  });
  const scope = identifyUri(rootUri);
  const containerRoots = new Set<string>();
  const taggedConflicts = new Set<string>();
  for (const row of knownRows) {
    if (row.kind !== 'entry') continue;
    const { tags } = row as FileTreeEntryRow & { tags?: readonly string[] };
    if (tags?.includes('has_conflict=true'))
      taggedConflicts.add(identifyUri(row.uri));
    if (row.type === 'directory' && tags?.includes('compile_target=true')) {
      const root = identifyUri(row.uri);
      if (row.access === 'container') containerRoots.add(root);
      else directories.push(root);
    }
  }
  for (const { to } of targets) {
    const root = identifyUri(to);
    if (within(root, scope)) directories.push(root);
    else if (within(scope, root)) directories.push(scope);
  }
  if (current?.kind === 'wiki' && current.isRoot)
    directories.push(identifyUri(current.uri));

  const { summaries, listingRoots } = useWikiConflictSummaries(
    enabled ? directories.filter((uri) => !containerRoots.has(uri)) : [],
    generation,
    requestOptions,
  );
  const coverage = [...summaries].sort(([a], [b]) => b.length - a.length);
  const [tagsAtSearch, setTagsAtSearch] = useState(
    () => new Map<WikiConflictSummary, ReadonlySet<string>>(),
  );
  // Newly loaded tags can reveal conflicts after a search result was cached.
  // A fresh result supersedes tags that were already present when it arrived.
  if (
    tagsAtSearch.size !== coverage.length ||
    coverage.some(([, summary]) => !tagsAtSearch.has(summary))
  )
    setTagsAtSearch(
      new Map(
        coverage.map(([, summary]) => [
          summary,
          tagsAtSearch.get(summary) ?? taggedConflicts,
        ]),
      ),
    );
  const taggedUris = [...taggedConflicts];
  return {
    listingRoots: [...listingRoots, ...containerRoots],
    presence: (target: string) => {
      const uri = identifyUri(target);
      for (const [directory, summary] of coverage) {
        if (!within(uri, directory)) continue;
        if (summary.files.some((file) => within(file, uri))) return true;
        if (!summary.limited) {
          const priorTags = tagsAtSearch.get(summary) ?? taggedConflicts;
          return taggedUris.some(
            (file) => within(file, uri) && !priorTags.has(file),
          );
        }
      }
    },
  };
}
