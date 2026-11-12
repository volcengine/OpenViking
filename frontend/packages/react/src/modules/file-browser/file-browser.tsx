import { LocaleProvider, useLocale } from '../../shared/locale-context.js';
import { forwardRef, useCallback, useMemo, useState } from 'react';
import type { CSSProperties } from 'react';

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
import { resolveLabels, resolvePreviewLabels } from './file-browser-labels.js';
import { useContainerWidth } from './use-container-width.js';
import { useFileBrowserSearchDialog } from './use-file-browser-search-dialog.js';
import { useFileBrowser } from './use-file-browser.js';
import { useFileBrowserAcl } from './use-file-browser-acl.js';
import { useFileBrowserHandle } from './use-file-browser-handle.js';
import type {
  FileBrowserComponent,
  FileBrowserHandle,
  FileBrowserProps,
} from './types.js';

export type {
  FileBrowserActions,
  FileBrowserApi,
  FileBrowserLabels,
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
export const FileBrowser: FileBrowserComponent = forwardRef<
  FileBrowserHandle,
  FileBrowserProps
>(function FileBrowser(
  {
    uri,
    defaultUri,
    rootUri = 'viking://',
    defaultExpandedUri,
    locale: localeProp,
    agentConnect,
    dashboardKinds,
    renderDashboard,
    acl,
    readOnly,
    previewActions,
    onUriChange,
    onImportResources,
    renderImportResources,
    previewSlots,
    labels,
    requestOptions,
    className,
    style,
    ...rest
  }: FileBrowserProps,
  ref,
) {
  const locale = useLocale(localeProp);
  const [internalUri, setInternalUri] = useState<string | null>(
    () => defaultUri ?? null,
  );
  const [preferredTreeWidth, setPreferredTreeWidth] =
    useState(TREE_DEFAULT_WIDTH);
  // `uri !== undefined` 才是受控：传了空字符串也算宿主接管，而不是「没传」。
  // 模块自身不会主动清空选中——点目录行与点面包屑祖先都带具体 URI，没有「取消
  // 选中」的交互——因此这里不需要为 `null` 单独留一条分支。
  //
  // `uri !== undefined` is what makes it controlled: even an empty string counts as
  // the host taking over rather than "absent". The module never clears the selection
  // on its own — both the row click and the breadcrumb ancestor carry a concrete URI,
  // and there is no "deselect" interaction — so no separate `null` branch is needed.
  const isControlled = uri !== undefined;
  const currentUri = isControlled ? uri : internalUri;
  const { ref: layoutRef, width: layoutWidth } =
    useContainerWidth<HTMLDivElement>();
  const stacked = layoutWidth > 0 && layoutWidth < SIDE_BY_SIDE_MIN_WIDTH;
  const text = resolveLabels(labels, locale, stacked);

  // 上限随容器变化：宿主把整个模块塞进窄栏时，树不能继续按 520px 抢地方。当前
  // 宽度再夹一次而不是丢掉，容器重新变宽时树会回到拖动过的位置。
  //
  // The cap follows the container: a host squeezing the module into a narrow column
  // must not have the tree keep claiming 520px. The current width is re-clamped
  // rather than discarded, so the tree returns to where it was dragged once the
  // container widens again.
  const treeMaxWidth = resolveTreeMaxWidth(layoutWidth);
  const treeWidth = Math.min(preferredTreeWidth, treeMaxWidth);

  // 引用必须稳定：它一路传到预览的取数 effect，每次渲染换新函数会让 effect
  // 反复重跑并取消上一次请求，预览永远加载不出来。
  //
  // The identity must stay stable: it reaches the preview's loading effect, and a
  // fresh function every render would re-run that effect and cancel the previous
  // request, leaving the preview permanently empty.
  const changeUri = useCallback(
    (next: string | null) => {
      if (!isControlled) setInternalUri(next);
      onUriChange?.(next);
    },
    [isControlled, onUriChange],
  );

  // 摘要文案只影响展示，不作为预览请求的依赖。
  // Summary copy changes presentation without restarting preview requests.
  const summaryLabels = useMemo(
    () => ({
      l0: text.l0Summary,
      l1: text.l1Overview,
      failure: text.summaryFailed,
    }),
    [text.l0Summary, text.l1Overview, text.summaryFailed],
  );

  // 宿主文案和语言未变时，保留预览覆盖对象的引用。
  // Preserve the preview overrides while the host copy and language are unchanged.
  const previewLabels = useMemo(
    () => resolvePreviewLabels(resolveLabels(labels, locale)),
    [labels, locale],
  );

  const browser = useFileBrowser({
    rootUri,
    locale,
    dashboardKinds,
    renderDashboard,
    dashboardLabels: labels?.dashboard,
    controlled: isControlled,
    summaryLabels,
    formatCharacterCount: text.formatCharacterCount,
    accessLabels: {
      accessDenied: text.accessDenied,
      traverseOnly: text.traverseOnly,
    },
    defaultExpandedUri,
    currentUri,
    requestOptions,
    onUriChange: changeUri,
  });
  const permissions = useFileBrowserAcl({
    uri: browser.currentUri,
    acl,
    locale,
    labels: labels?.acl,
    requestOptions,
    refresh: browser.actions.refresh,
  });
  const navigation = useFileBrowserHandle(browser, ref);

  const search = useFileBrowserSearchDialog(layoutRef);
  const baseClass = stacked
    ? 'ov-file-browser ov-file-browser--stacked'
    : 'ov-file-browser';

  return (
    <LocaleProvider locale={locale}>
      <div
        {...rest}
        ref={layoutRef}
        className={className ? `${baseClass} ${className}` : baseClass}
        {...search.shortcut}
        style={
          {
            ...style,
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
            rootUri={rootUri}
            entries={{
              agentConnect,
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
      <FileBrowserSearch
        key={search.session}
        open={search.open}
        locale={locale}
        rootUri={rootUri}
        triggerRef={layoutRef}
        text={text}
        requestOptions={requestOptions}
        advancedOpen={search.advanced}
        onToggleAdvanced={search.setAdvanced}
        onOpenResource={navigation.openResource}
        onClose={search.close}
      />
    </LocaleProvider>
  );
});
