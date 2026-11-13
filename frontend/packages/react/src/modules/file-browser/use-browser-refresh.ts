import { useCallback, useRef, useState } from 'react';

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
  reloadTree: () => Promise<boolean>,
  reveal: (uri: string) => Promise<boolean>,
  selectedUri: string | null,
) {
  const [refreshing, setRefreshing] = useState(false);
  const [generation, setGeneration] = useState(0);
  const latestUri = useRef(selectedUri);
  latestUri.current = selectedUri;
  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      setGeneration((value) => value + 1);
      // 刷新被账号切换打断时，`selectedUri` 和 `reveal` 还属于切换前的账号，
      // 继续定位会把旧账号的目录响应写进新账号的树。
      // When an account switch interrupted the reload, `selectedUri` and `reveal`
      // still belong to the previous account; revealing would write that account's
      // listings into the new account's tree.
      if (!(await reloadTree())) return;
      // 刷新期间用户已经打开了别的资源：再定位旧的那个会取消新的导航。
      // The user opened another resource while this ran; revealing the old one would
      // cancel the new navigation.
      if (selectedUri && latestUri.current === selectedUri)
        await reveal(selectedUri);
    } finally {
      setRefreshing(false);
    }
  }, [reloadTree, reveal, selectedUri]);
  return { refreshing, generation, refresh };
}
