import { useCallback, useState } from 'react';

/**
 * 外部改动后同时刷新目录与预览。URI 和客户端都没变时，请求也要重新发出，所以
 * 用一个共享的 generation 让两边的加载失效。
 *
 * Refreshes the directory and the preview together after external changes. When the
 * URI and client are unchanged the requests must still be re-issued, so a shared
 * generation invalidates both loads.
 */
export function useBrowserRefresh(reloadTree: () => Promise<void>) {
  const [refreshing, setRefreshing] = useState(false);
  const [generation, setGeneration] = useState(0);
  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      setGeneration((value) => value + 1);
      await reloadTree();
    } finally {
      setRefreshing(false);
    }
  }, [reloadTree]);
  return { refreshing, generation, refresh };
}
