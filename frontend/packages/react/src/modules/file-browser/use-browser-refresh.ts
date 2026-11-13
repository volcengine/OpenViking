import { useCallback, useState } from 'react';

/**
 * 外部改动后同时刷新目录与预览。URI 和客户端都没变时，请求也要重新发出，所以
 * 用一个共享的 generation 让两边的加载失效。
 *
 * 刷新会清空已加载的列表，只重新读展开目录的首页；选中的资源可能在后面的页上，
 * 所以刷新后要重新定位，否则预览还在、树上的选中行却消失了。
 *
 * Refreshes the directory and the preview together after external changes. When the
 * URI and client are unchanged the requests must still be re-issued, so a shared
 * generation invalidates both loads.
 *
 * A refresh clears the loaded listings and re-reads only the first page of each
 * expanded directory. The selection may live on a later page, so it is revealed
 * again; otherwise the preview would stay while its tree row disappears.
 */
export function useBrowserRefresh(
  reloadTree: () => Promise<void>,
  reveal: (uri: string) => Promise<boolean>,
  selectedUri: string | null,
) {
  const [refreshing, setRefreshing] = useState(false);
  const [generation, setGeneration] = useState(0);
  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      setGeneration((value) => value + 1);
      await reloadTree();
      if (selectedUri) await reveal(selectedUri);
    } finally {
      setRefreshing(false);
    }
  }, [reloadTree, reveal, selectedUri]);
  return { refreshing, generation, refresh };
}
