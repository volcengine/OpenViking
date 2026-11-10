import type { Locale } from '../../shared/locale.js';
import type { ReactNode } from 'react';

import { FileTree } from '../../components/file-tree/index.js';
import type {
  FileTreeEntryRow,
  FileTreeLabels,
} from '../../components/file-tree/index.js';
import { FileBrowserToolbar } from './file-browser-toolbar.js';
import { fileTypeIcon } from './fs-icon.js';
import type { FileBrowserApi } from './types.js';

/**
 * 左栏：宿主入口条、搜索行与目录树。
 *
 * 宽度由外层的可拖拽分隔条决定，因此这里只铺满自己的盒子，不设宽度；树内容
 * 比栏宽时横向滚动，而不是把名称截成省略号。
 *
 * The left column: the host's entries, the search row and the tree.
 *
 * The width comes from the draggable divider outside, so this only fills its own
 * box. When the tree is wider than the column it scrolls sideways rather than
 * truncating names into ellipses.
 */
export interface FileBrowserTreePaneProps {
  toolbarActions?: ReactNode;
  locale: Locale;
  searchId: string;
  browser: FileBrowserApi;
  treeLabels: FileTreeLabels | undefined;
  searchPlaceholder: string;
  refreshLabel: string;
  searchLoading: string;
  searchEmpty: string;
  /** 搜索失败时的结论文案。Conclusion shown when the search request fails. */
  searchErrorLabel: string;
  /** 失败态里的重试按钮。Retry button of the failure state. */
  retryLabel: string;
}

export function FileBrowserTreePane({
  toolbarActions,
  locale,
  searchId,
  browser,
  treeLabels,
  searchPlaceholder,
  refreshLabel,
  searchLoading,
  searchEmpty,
  searchErrorLabel,
  retryLabel,
}: FileBrowserTreePaneProps) {
  return (
    <>
      {toolbarActions ? (
        <div className="ov-file-browser__entries">{toolbarActions}</div>
      ) : null}

      <FileBrowserToolbar
        searchId={searchId}
        query={browser.query}
        refreshing={browser.refreshing}
        canRefresh={browser.isReady}
        placeholder={searchPlaceholder}
        refreshLabel={refreshLabel}
        onQueryChange={browser.actions.setQuery}
        onRefresh={() => void browser.actions.refresh()}
      />
      <div className="ov-file-browser__tree-scroll">
        <FileTree
          locale={locale}
          rows={browser.rows}
          selectedId={browser.selectedId}
          isReady={browser.isReady}
          labels={treeLabels}
          // 搜索失败要单独显示：`useFsSearch` 失败时把结果置空并给出 error，若这里
          // 只看 `searching`/`searchLoading`，一次 403 会渲染成「没有匹配结果」，
          // 用户既看不到原因也没有重试入口。
          //
          // A failed search gets its own state: `useFsSearch` clears the results and
          // reports an error, and reading only `searching`/`searchLoading` would render
          // a 403 as "no matching resources", with neither the reason nor a retry.
          emptyFallback={
            browser.searching ? (
              browser.searchError ? (
                // 结论用搜索专属文案，原因用服务端或传输层给的原话：只说「加载失败」
                // 会让一次权限错误与后端故障看起来一样，而这句「加载目录失败」更是
                // 答错了问题——失败的是搜索，不是目录。
                //
                // The conclusion uses search-specific copy and the reason is the server's
                // or the transport's own wording: a bare "failed to load" makes a
                // permission error look like a backend fault, and "failed to load the
                // directory" answers the wrong question — it is the search that failed.
                <div className="ov-file-browser__hint ov-file-browser__hint--error">
                  <p className="ov-file-browser__failure-title">
                    {searchErrorLabel}
                  </p>
                  <p className="ov-file-browser__failure-detail">
                    {browser.searchError.message}
                  </p>
                  <button
                    type="button"
                    className="ov-file-browser__retry"
                    onClick={() => void browser.actions.refresh()}
                  >
                    {retryLabel}
                  </button>
                </div>
              ) : (
                <p className="ov-file-browser__hint">
                  {browser.searchLoading ? searchLoading : searchEmpty}
                </p>
              )
            ) : undefined
          }
          renderRowIcon={(row: FileTreeEntryRow) => fileTypeIcon(row.name)}
          operations={{
            select: browser.actions.select,
            toggleExpand: browser.actions.toggleExpand,
            loadMore: browser.actions.loadMore,
            retry: browser.actions.retry,
          }}
        />
      </div>
    </>
  );
}
