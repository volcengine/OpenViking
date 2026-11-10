import { useCallback, useId, useMemo, useState } from 'react';
import type { CSSProperties } from 'react';

import { ResizableHandle } from '../../shared/components/resizable/index.js';
import { FileBrowserDetail } from './file-browser-detail.js';
import { FileBrowserEmpty } from './file-browser-empty.js';
import { FileBrowserFailure } from './file-browser-failure.js';
import {
  TREE_DEFAULT_WIDTH,
  TREE_MIN_WIDTH,
  resolveTreeMaxWidth,
} from './file-browser-layout.js';
import { FileBrowserTreePane } from './file-browser-tree-pane.js';
import { resolveLabels, resolvePreviewLabels } from './file-browser-labels.js';
import { useContainerWidth } from './use-container-width.js';
import { useFileBrowser } from './use-file-browser.js';
import type { FileBrowserProps } from './types.js';

export type {
  FileBrowserActions,
  FileBrowserApi,
  FileBrowserLabels,
  FileBrowserProps,
} from './types.js';

/**
 * 文件浏览模块的组合视图，对齐控制台设计稿：左侧目录树（入口条、搜索行、树），
 * 右侧详情区（面包屑、标题行、正文、底部元信息）。两栏之间有一条可拖拽的
 * 分隔条，拖动改树宽；取数与联动都在 {@link useFileBrowser} 里，这里只组装
 * 视图。
 *
 * The file browser module's composite view, matching the console design: the tree
 * on the left (entry row, search row, tree) and the detail pane on the right
 * (breadcrumb, header, body, footer). A draggable divider between them resizes the
 * tree; loading and coordination live in {@link useFileBrowser}, and this only
 * assembles the view.
 */
export function FileBrowser({
  uri,
  defaultUri,
  rootUri = 'viking://',
  defaultExpandedUri,
  locale,
  agentConnect,
  onUriChange,
  onImportResources,
  renderImportResources,
  labels,
  requestOptions,
  className,
  style,
  ...rest
}: FileBrowserProps) {
  const [internalUri, setInternalUri] = useState<string | null>(
    () => defaultUri ?? null,
  );
  const [preferredTreeWidth, setPreferredTreeWidth] =
    useState(TREE_DEFAULT_WIDTH);
  const searchId = useId();
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
  const text = resolveLabels(labels, locale);
  const { ref: layoutRef, width: layoutWidth } =
    useContainerWidth<HTMLDivElement>();

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
    controlled: isControlled,
    summaryLabels,
    formatCharacterCount: text.formatCharacterCount,
    defaultExpandedUri,
    currentUri,
    requestOptions,
    onUriChange: changeUri,
  });

  return (
    <div
      {...rest}
      ref={layoutRef}
      className={['ov-file-browser', className].filter(Boolean).join(' ')}
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
          entries={{
            agentConnect,
            onImportResources,
            renderImportResources,
            labels: labels?.entries,
          }}
          searchId={searchId}
          browser={browser}
          treeLabels={text.tree}
          locale={locale}
          searchPlaceholder={text.searchPlaceholder}
          refreshLabel={text.refresh}
          searchLoading={text.searchLoading}
          searchEmpty={text.searchEmpty}
          searchErrorLabel={text.searchFailed}
          retryLabel={text.retry}
        />
      </div>

      <ResizableHandle
        label={text.resizeTree}
        min={TREE_MIN_WIDTH}
        max={treeMaxWidth}
        value={treeWidth}
        onResize={setPreferredTreeWidth}
        className="ov-file-browser__resizer"
      />

      {browser.previewTabs && browser.previewTabs.length > 0 ? (
        <FileBrowserDetail
          tabs={browser.previewTabs}
          activeKey={browser.previewKey}
          locale={locale}
          labels={previewLabels}
          footer={browser.previewFooter}
          viewLabels={{
            preview: text.previewMode,
            source: text.sourceMode,
            copyPath: text.copyPath,
            copyContent: text.copyContent,
            download: text.download,
            viewSwitch: text.viewSwitch,
          }}
          onActiveKeyChange={browser.actions.setPreviewKey}
        />
      ) : // 读失败与「没选中」是两件事：前者有选中的资源却取不到内容，必须显示
      // 原因与重试；后者才是空态插画。把失败画成空态会让一次 403 看起来像「还
      // 没选文件」。
      //
      // A read failure and "nothing selected" are two different things: the former
      // has a selected resource it could not load and needs the reason plus a retry,
      // while only the latter is the empty-state artwork. Painting a failure as the
      // empty state makes a 403 look like "no file selected yet".
      browser.currentUri && browser.previewError ? (
        <div className="ov-file-browser__detail">
          <FileBrowserFailure
            message={text.error}
            detail={browser.previewError.message}
            retryLabel={text.retry}
            onRetry={() => void browser.actions.refresh()}
          />
        </div>
      ) : (
        <div className="ov-file-browser__detail">
          <FileBrowserEmpty label={text.empty} />
        </div>
      )}
    </div>
  );
}
