import { Button } from '../../shared/components/button/button.js';
import type { Ref } from 'react';
import type { FileTreeHandle } from '../../components/file-tree/index.js';
import type { Locale } from '../../shared/locale.js';
import { dashboardLabels as resolveDashboardLabels } from './dashboard-labels.js';

import { FileBrowserEntries } from '../../components/file-browser-entries/index.js';
import type { FileBrowserEntriesProps } from '../../components/file-browser-entries/index.js';
import { FileTree } from '../../components/file-tree/index.js';
import type {
  FileTreeEntryRow,
  FileTreeLabels,
} from '../../components/file-tree/index.js';
import { FileBrowserToolbar } from './file-browser-toolbar.js';
import { WikiBookIcon } from '../../icons/index.js';
import { DashboardIndicator } from './dashboard-indicator.js';
import { fileTypeIcon } from './fs-icon.js';
import type { FileBrowserApi, FileBrowserDashboardLabels } from './types.js';

/**
 * 左栏：内置入口条、搜索行与目录树。
 *
 * 宽度由外层的可拖拽分隔条决定，因此这里只铺满自己的盒子，不设宽度；树内容
 * 比栏宽时横向滚动，而不是把名称截成省略号。
 *
 * The left column: the built-in entries, the search row and the tree.
 *
 * The width comes from the draggable divider outside, so this only fills its own
 * box. When the tree is wider than the column it scrolls sideways rather than
 * truncating names into ellipses.
 */
export interface FileBrowserTreePaneProps {
  treeRef?: Ref<FileTreeHandle>;
  entries: Pick<
    FileBrowserEntriesProps,
    'agentConnect' | 'onImportResources' | 'renderImportResources' | 'labels'
  >;
  locale: Locale;
  dashboardLabels?: FileBrowserDashboardLabels;
  onSearch?: () => void;
  browser: FileBrowserApi;
  rootUri: string;
  treeLabels: FileTreeLabels | undefined;
  searchPlaceholder: string;
  refreshLabel: string;
  /** 失败态里的重试按钮。Retry button of the failure state. */
  retryLabel: string;
}

export function FileBrowserTreePane({
  treeRef,
  entries,
  locale,
  dashboardLabels,
  onSearch,
  browser,
  rootUri,
  treeLabels,
  searchPlaceholder,
  refreshLabel,
  retryLabel,
}: FileBrowserTreePaneProps) {
  return (
    <>
      <FileBrowserEntries
        {...entries}
        locale={locale}
        className="ov-file-browser__entries"
      />

      <FileBrowserToolbar
        onSearch={onSearch}
        refreshing={browser.refreshing}
        canRefresh={browser.isReady}
        placeholder={searchPlaceholder}
        refreshLabel={refreshLabel}
        onRefresh={() => void browser.actions.refresh()}
      />
      <div className="ov-file-browser__tree-scroll">
        {browser.dashboardError ? (
          <div
            className="ov-file-browser__hint ov-file-browser__hint--error"
            aria-live="polite"
          >
            <p>
              {resolveDashboardLabels(locale, dashboardLabels).failed}:{' '}
              {browser.dashboardError.message}
            </p>
            <Button
              size="small"
              type="default"
              htmlType="button"
              onClick={() => void browser.actions.refresh()}
            >
              {retryLabel}
            </Button>
          </div>
        ) : null}
        <FileTree
          ref={treeRef}
          search={false}
          refresh={false}
          locale={locale}
          rows={browser.rows}
          rootOrder={rootUri === 'viking://' ? 'context' : 'input'}
          selectedId={browser.selectedId}
          isReady={browser.isReady}
          labels={treeLabels}
          renderRowIcon={(row: FileTreeEntryRow) =>
            browser.dashboards.get(row.uri)?.isRoot ? (
              <WikiBookIcon width={14} height={14} />
            ) : (
              fileTypeIcon(row.name)
            )
          }
          renderDashboard={(row) => {
            const dashboard = browser.dashboards.get(row.uri);
            return dashboard ? (
              <DashboardIndicator
                dashboard={dashboard}
                locale={locale}
                labels={dashboardLabels}
                onOpen={browser.actions.openDashboard}
              />
            ) : null;
          }}
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
