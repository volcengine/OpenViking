import { useRenderErrors } from './file-browser-labels.js';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { Button } from '../../shared/primitives/button/button.js';
import type { Ref } from 'react';
import type { Locale } from '../../shared/locale.js';
import { dashboardLabels as resolveDashboardLabels } from './dashboard-labels.js';

import { FileBrowserActionBar } from '../../components/file-browser-action-bar/index.js';
import type { FileBrowserActionBarProps } from '../../components/file-browser-action-bar/index.js';
import { FileTree } from '../../components/file-tree/index.js';
import type {
  FileTreeEntryRow,
  FileTreeHandle,
  FileTreeLabels,
} from '../../components/file-tree/index.js';
import { FileBrowserToolbar } from './file-browser-toolbar.js';
import { WikiBookIcon } from '../../icons/index.js';
import { DashboardIndicator } from './dashboard-indicator.js';
import { fileTypeIcon } from './fs-icon.js';
import type { FileBrowserApi, FileBrowserDashboardLabels } from './types.js';

/**
 * 左栏：内置操作栏、搜索行与目录树。
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
  treeScrollRef?: Ref<HTMLDivElement>;
  entries: Pick<
    FileBrowserActionBarProps,
    | 'agentConnect'
    | 'agentConnectOpen'
    | 'onAgentConnectOpenChange'
    | 'onImportResources'
    | 'renderImportResources'
    | 'labels'
  >;
  locale: Locale;
  dashboardLabels?: FileBrowserDashboardLabels;
  onSearch: () => void;
  onAdvancedSearch: () => void;
  advancedOpen: boolean;
  browser: FileBrowserApi;
  rootUri: string;
  treeLabels: FileTreeLabels | undefined;
  searchPlaceholder: string;
  advancedLabel: string;
  refreshLabel: string;
  /** 失败态里的重试按钮。Retry button of the failure state. */
  retryLabel: string;
}

function FileBrowserTreePaneContent({
  treeRef,
  treeScrollRef,
  entries,
  locale,
  dashboardLabels,
  onSearch,
  onAdvancedSearch,
  advancedOpen,
  browser,
  rootUri,
  treeLabels,
  searchPlaceholder,
  advancedLabel,
  refreshLabel,
  retryLabel,
}: FileBrowserTreePaneProps) {
  return (
    <>
      <FileBrowserActionBar
        {...entries}
        locale={locale}
        className="ov-file-browser__entries"
      />

      <FileBrowserToolbar
        onSearch={onSearch}
        onAdvanced={onAdvancedSearch}
        advancedOpen={advancedOpen}
        refreshing={browser.refreshing}
        canRefresh={browser.isReady}
        placeholder={searchPlaceholder}
        advancedLabel={advancedLabel}
        refreshLabel={refreshLabel}
        onRefresh={() => void browser.actions.refresh()}
      />
      <div ref={treeScrollRef} className="ov-file-browser__tree-scroll">
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
          renderRowTrailing={(row) => {
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

export function FileBrowserTreePane(props: FileBrowserTreePaneProps) {
  return (
    <ErrorBoundary
      message={useRenderErrors(props.locale).tree}
      locale={props.locale}
      resetKeys={[props.rootUri]}
    >
      <FileBrowserTreePaneContent {...props} />
    </ErrorBoundary>
  );
}
