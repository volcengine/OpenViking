import { useMemo, useState } from 'react';
import type { FileTreeRow } from '../../components/file-tree/index.js';
import type { FsTreeState } from './tree-types.js';

export function useFileBrowserTreeActions({
  loadMore,
  retry,
  refresh,
}: Pick<FsTreeState, 'loadMore' | 'retry' | 'refresh'>) {
  const [refreshing, setRefreshing] = useState(false);
  // Refresh the preview with the tree even if the selected URI is unchanged.
  const [refreshGeneration, setRefreshGeneration] = useState(0);
  const actions = useMemo(
    () => ({
      loadMore: async (row: FileTreeRow) => {
        if ('parentId' in row && row.parentId) await loadMore(row.parentId);
      },
      retry: async (row: FileTreeRow) => {
        if ('parentId' in row && row.parentId) await retry(row.parentId);
      },
      refresh: async () => {
        setRefreshing(true);
        try {
          setRefreshGeneration((generation) => generation + 1);
          await refresh();
        } finally {
          setRefreshing(false);
        }
      },
    }),
    [loadMore, retry, refresh],
  );

  return { actions, refreshing, refreshGeneration };
}
