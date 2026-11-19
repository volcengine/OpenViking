import { useExposureRef } from '../../shared/analytics/use-exposure.js';
import { LocaleProvider, useLocale } from '../../shared/locale-context.js';
import { forwardRef, useMemo, useState, type CSSProperties } from 'react';

import { ResizableHandle } from '../../shared/primitives/resizable/index.js';
import { FileBrowserPreviewPane } from './file-browser-preview-pane.js';
import {
  SIDE_BY_SIDE_MIN_WIDTH,
  TREE_DEFAULT_WIDTH,
  TREE_MIN_WIDTH,
  resolveTreeMaxWidth,
} from './file-browser-layout.js';
import { FileBrowserTreePane } from './file-browser-tree-pane.js';
import { FileBrowserSearch } from './file-browser-search.js';
import { resolveLabels, resolvePaneLabels } from './file-browser-labels.js';
import { useContainerWidth } from './use-container-width.js';
import { useFileBrowserSearchDialog } from './use-file-browser-search-dialog.js';
import { useFileBrowser } from './use-file-browser.js';
import { useFileBrowserAcl } from './use-file-browser-acl.js';
import { usePackImportPicker } from './use-pack-import-picker.js';
import { useFileBrowserHandle } from './use-file-browser-handle.js';
import { useFileBrowserUri } from './use-file-browser-uri.js';
import type {
  FileBrowserComponent,
  FileBrowserHandle,
  FileBrowserProps,
} from './types.js';

/**
 * 文件浏览模块的组合视图：左侧目录树（操作栏、搜索行、树），
 * 右侧详情区（面包屑、标题行、正文、底部元信息）。两栏之间有一条可拖拽的
 * 分隔条，拖动改树宽；取数与联动都在 {@link useFileBrowser} 里，这里只组装
 * 视图。
 *
 * The file browser module's composite view: the tree
 * on the left (action bar, search row, tree) and the detail pane on the right
 * (breadcrumb, header, body, footer). A draggable divider between them resizes the
 * tree; loading and coordination live in {@link useFileBrowser}, and this only
 * assembles the view.
 */
export const FileBrowserContent: FileBrowserComponent = forwardRef<
  FileBrowserHandle,
  FileBrowserProps
>(function FileBrowser(
  {
    uri,
    defaultUri,
    rootUri = 'viking://',
    defaultExpandedUri,
    defaultExpandedUris,
    defaultExpansionSnapshot,
    locale: localeProp,
    agentConnect,
    agentConnectOpen,
    onAgentConnectOpenChange,
    dashboardKinds,
    experienceDataSource,
    experienceDashboardSlots,
    renderDashboard,
    acl,
    readOnly,
    previewActions,
    onUriChange,
    onSelect,
    onExpandedUrisChange,
    onExpansionSnapshotChange,
    onImportResources,
    renderImportResources,
    previewSlots,
    labels,
    requestOptions,
    ...rest
  }: FileBrowserProps,
  ref,
) {
  const locale = useLocale(localeProp);
  const { currentUri, isControlled, reportedUri, changeUri } =
    useFileBrowserUri(uri, defaultUri, onUriChange);
  const [preferredTreeWidth, setPreferredTreeWidth] =
    useState(TREE_DEFAULT_WIDTH);
  const { ref: layoutRef, width } = useContainerWidth<HTMLDivElement>();
  const stacked = width > 0 && width < SIDE_BY_SIDE_MIN_WIDTH;
  const text = resolveLabels(labels, locale, stacked);

  // 上限随容器变化：宿主把整个模块塞进窄栏时，树不能继续按 520px 抢地方。当前
  // 宽度再夹一次而不是丢掉，容器重新变宽时树会回到拖动过的位置。
  //
  // The cap follows the container: a host squeezing the module into a narrow column
  // must not have the tree keep claiming 520px. The current width is re-clamped
  // rather than discarded, so the tree returns to where it was dragged once the
  // container widens again.
  const treeMaxWidth = resolveTreeMaxWidth(width);
  const treeWidth = Math.min(preferredTreeWidth, treeMaxWidth);

  // Keep preview and summary copy stable until locale or host overrides change.
  const { summaryLabels, previewLabels } = useMemo(
    () => resolvePaneLabels(labels, locale),
    [labels, locale],
  );
  const browser = useFileBrowser({
    rootUri,
    locale,
    dashboardKinds,
    experienceDataSource,
    experienceDashboardSlots,
    renderDashboard,
    dashboardLabels: labels?.dashboard,
    controlled: isControlled,
    summaryLabels,
    formatCharacterCount: text.formatCharacterCount,
    accessLabels: text,
    defaultExpandedUri,
    defaultExpandedUris,
    defaultExpansionSnapshot,
    onExpandedUrisChange,
    onExpansionSnapshotChange,
    currentUri,
    requestOptions,
    onUriChange: changeUri,
    onSelect,
  });
  const permissions = useFileBrowserAcl({
    uri: browser.currentUri,
    acl,
    readOnly,
    locale,
    labels: labels?.acl,
    requestOptions,
    refresh: browser.actions.refresh,
  });
  const pack = usePackImportPicker(layoutRef, text, locale, permissions);
  const navigation = useFileBrowserHandle(
    browser,
    ref,
    rootUri,
    uri,
    reportedUri,
    pack.choosePackFile,
    defaultExpansionSnapshot !== undefined || defaultExpandedUris !== undefined,
  );

  const search = useFileBrowserSearchDialog(layoutRef);
  const baseClass = stacked
    ? 'ov-file-browser ov-file-browser--stacked'
    : 'ov-file-browser';

  const exposure = useExposureRef('file_browser_show', layoutRef);
  return (
    <LocaleProvider locale={locale}>
      <div
        {...rest}
        ref={exposure}
        className={[baseClass, rest.className].filter(Boolean).join(' ')}
        {...search.shortcut}
        style={
          {
            ...rest.style,
            // 分隔条是独立一列，树宽只作用在自己身上，两者相加才是左栏占用。
            // The divider is its own column: the tree width applies to the tree alone
            // and the two together are what the left side occupies.
            '--ov-file-browser-tree-width': `${treeWidth}px`,
          } as CSSProperties
        }
      >
        <div className="ov-file-browser__tree">
          <FileBrowserTreePane
            treeRef={navigation.treeRef}
            treeScrollRef={navigation.treeScrollRef}
            rootUri={rootUri}
            entries={{
              agentConnect,
              agentConnectOpen,
              onAgentConnectOpenChange,
              onImportResources,
              renderImportResources,
              labels: labels?.actionBar,
            }}
            onSearch={search.show}
            onAdvancedSearch={search.showAdvanced}
            advancedOpen={search.advanced && search.open}
            browser={browser}
            treeLabels={text.tree}
            locale={locale}
            dashboardLabels={labels?.dashboard}
            searchPlaceholder={text.searchPlaceholder}
            advancedLabel={text.searchAdvancedLabel}
            refreshLabel={text.refresh}
            retryLabel={text.retry}
          />
        </div>

        {!stacked ? (
          <ResizableHandle
            label={text.resizeTree}
            min={TREE_MIN_WIDTH}
            max={treeMaxWidth}
            value={treeWidth}
            onResize={setPreferredTreeWidth}
            className="ov-file-browser__resizer"
          />
        ) : null}

        <FileBrowserPreviewPane
          browser={browser}
          permissions={permissions}
          rootUri={rootUri}
          locale={locale}
          text={text}
          previewLabels={previewLabels}
          readOnly={readOnly}
          previewActions={previewActions}
          previewSlots={previewSlots}
          requestOptions={requestOptions}
        />
      </div>
      {permissions.drawer}
      {pack.dialog}
      <FileBrowserSearch
        key={search.session}
        {...search.dialog}
        locale={locale}
        rootUri={rootUri}
        text={text}
        requestOptions={requestOptions}
        onOpenResource={(target) => {
          void navigation.openResource(target).catch(() => {});
        }}
      />
    </LocaleProvider>
  );
});
