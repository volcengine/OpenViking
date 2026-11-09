import { useCallback, useId, useMemo, useState } from 'react';

import { FileTree } from '../../components/file-tree/index.js';
import { FileBrowserDetail } from './file-browser-detail.js';
import { FileBrowserEmpty } from './file-browser-empty.js';
import { FileBrowserToolbar } from './file-browser-toolbar.js';
import { fileTypeIcon } from './fs-icon.js';
import { resolveLabels, resolvePreviewLabels } from './file-browser-labels.js';
import { resolveTreeLabels } from './tree-info-copy.js';
import { useFileBrowser } from './use-file-browser.js';
import type { FileBrowserProps } from './types.js';

export type {
  FileBrowserActions,
  FileBrowserApi,
  FileBrowserLabels,
  FileBrowserProps,
} from './types.js';

/**
 * 文件浏览模块的组合视图，对齐控制台设计稿：左侧 352px 的目录树（入口条、
 * 搜索行、树），右侧详情区（面包屑、标题行、正文、底部元信息）。取数与联动
 * 都在 {@link useFileBrowser} 里，这里只组装视图。
 *
 * The file browser module's composite view, matching the console design: a 352px
 * tree on the left (entry row, search row, tree) and the detail pane on the right
 * (breadcrumb, header, body, footer). Loading and coordination live in
 * {@link useFileBrowser}; this only assembles the view.
 */
export function FileBrowser({
  uri,
  defaultUri,
  rootUri = 'viking://',
  defaultExpandedUri,
  locale,
  toolbarActions,
  onUriChange,
  labels,
  requestOptions,
  className,
  ...rest
}: FileBrowserProps) {
  const [internalUri, setInternalUri] = useState<string | null>(
    () => defaultUri ?? null,
  );
  const searchId = useId();
  const currentUri = uri !== undefined ? (uri ?? null) : internalUri;
  const text = resolveLabels(labels);

  // 引用必须稳定：它一路传到预览的取数 effect，每次渲染换新函数会让 effect
  // 反复重跑并取消上一次请求，预览永远加载不出来。
  //
  // The identity must stay stable: it reaches the preview's loading effect, and a
  // fresh function every render would re-run that effect and cancel the previous
  // request, leaving the preview permanently empty.
  const changeUri = useCallback(
    (next: string | null) => {
      if (uri === undefined) setInternalUri(next);
      onUriChange?.(next);
    },
    [uri, onUriChange],
  );

  // 文案对象必须稳定：每次渲染都新建会让取数 effect 反复重跑并取消上一次请求。
  // The copy object must be stable, or the loading effect re-runs and cancels itself.
  const summaryLabels = useMemo(
    () => ({ l0: text.l0Summary, l1: text.l1Overview }),
    [text.l0Summary, text.l1Overview],
  );

  // 依赖 `labels` 而不是解析后的 `text`：后者每次渲染都是新对象，会让这个 memo
  // 失效，进而让预览的取数 effect 反复重跑并取消请求。
  //
  // Depend on `labels`, not on the resolved `text`: the latter is a fresh object
  // every render, which would void this memo and make the preview's loading effect
  // re-run and cancel itself.
  const previewLabels = useMemo(
    () => resolvePreviewLabels(resolveLabels(labels)),
    [labels],
  );

  const browser = useFileBrowser({
    rootUri,
    summaryLabels,
    defaultExpandedUri,
    currentUri,
    requestOptions,
    onUriChange: changeUri,
  });

  return (
    <div
      {...rest}
      className={['ov-file-browser', className].filter(Boolean).join(' ')}
    >
      <div className="ov-file-browser__tree">
        {toolbarActions ? (
          <div className="ov-file-browser__entries">{toolbarActions}</div>
        ) : null}

        <FileBrowserToolbar
          searchId={searchId}
          query={browser.query}
          refreshing={browser.refreshing}
          canRefresh={browser.isReady}
          placeholder={text.searchPlaceholder}
          refreshLabel={text.refresh}
          onQueryChange={browser.actions.setQuery}
          onRefresh={() => void browser.actions.refresh()}
        />
        <div className="ov-file-browser__tree-scroll">
          <FileTree
            rows={browser.rows}
            selectedId={browser.selectedId}
            isReady={browser.isReady}
            labels={resolveTreeLabels(locale, text.tree)}
            emptyFallback={
              browser.searching ? (
                <p className="ov-file-browser__hint">
                  {browser.searchLoading
                    ? text.searchLoading
                    : text.searchEmpty}
                </p>
              ) : undefined
            }
            renderRowIcon={({ name }) => fileTypeIcon(name)}
            operations={{
              select: browser.actions.select,
              toggleExpand: browser.actions.toggleExpand,
              loadMore: browser.actions.loadMore,
              retry: browser.actions.retry,
            }}
          />
        </div>
      </div>

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
      ) : (
        // 空态图只在真正没选中资源时出现。加载中由页签自己带 `loading` 处理，
        // 外壳因此常驻，切文件不会闪一屏。
        //
        // The empty-state artwork belongs to "nothing selected" only. Loading is
        // carried by the tab's own `loading` flag, so the shell stays put and a file
        // switch does not flash the whole area.

        <div className="ov-file-browser__detail">
          <FileBrowserEmpty label={text.empty} />
        </div>
      )}
    </div>
  );
}
