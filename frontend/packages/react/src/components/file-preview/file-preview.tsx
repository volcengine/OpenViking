import { forwardRef, useCallback, useImperativeHandle, useState } from 'react';

import type { MarkdownAdapterOptions } from '@ark-design/ui/file-viewer/adapters/markdown';

import { mergeLabels } from '../../shared/locale.js';
import { FilePreviewPanels } from './file-preview-panels.js';
import { useFilePreviewViewState } from './use-file-preview-view-state.js';
import { useFilePreviewLevels } from './use-file-preview-levels.js';
import { resolveFilePreviewLabels } from './file-preview-labels.js';
import { FilePreviewTabRow } from './file-preview-tab-row.js';
import { useFilePreviewActions } from './use-file-preview-actions.js';
import { useFilePreviewActionsContext } from './use-file-preview-actions-context.js';
import { useFilePreviewIds } from './use-file-preview-ids.js';
import { useDirectoryLevel } from './use-directory-level.js';
import { useFilePreviewEditing } from './use-file-preview-editing.js';
import type {
  FilePreviewComponent,
  FilePreviewContentTab,
  FilePreviewDirectoryTab,
  FilePreviewFileTab,
  FilePreviewHandle,
  FilePreviewProps,
  FilePreviewTab,
} from './types.js';

export { LoadingState } from './file-preview-state.js';

function isFileTab(tab: FilePreviewTab): tab is FilePreviewFileTab {
  return tab.kind === 'file';
}

/**
 * 文件预览：页签行、内容区标题行与正文组成的外壳，正文由底座按格式渲染。
 *
 * 文件页签的正文交给 `FileViewerCore`，内容页签的正文由宿主原样渲染。空文件、
 * 截断与编辑态由组件自己处理，不经过底座。
 *
 * File preview: a shell of tab row, content header and body, where the body is
 * rendered by the base component according to the format. A file tab hands its
 * body to `FileViewerCore`; a content tab renders the host node as-is. Empty
 * files, truncation and editing are handled by this component, not the base.
 */
export const FilePreview: FilePreviewComponent = forwardRef<
  FilePreviewHandle,
  FilePreviewProps
>(function FilePreview(
  {
    tabs,
    activeKey,
    defaultActiveKey,
    locale,
    labels,
    onActiveKeyChange,
    className,
    ...rest
  },
  ref,
) {
  const text = resolveFilePreviewLabels(locale, labels);
  const [internalKey, setInternalKey] = useState(
    () => defaultActiveKey ?? tabs[0].key,
  );
  const currentKey = activeKey ?? internalKey;
  const selectedTab = tabs.find((tab) => tab.key === currentKey) ?? tabs[0];
  const {
    tab: activeTab,
    levelKey,
    selectLevel,
  } = useFilePreviewLevels(selectedTab);
  const directory = useDirectoryLevel(
    activeTab.kind === 'directory' ? activeTab : undefined,
  );
  const {
    viewMode,
    setViewMode,
    viewModes,
    setViewModes,
    viewerHandle,
    onHandleChange,
    onViewModesChange,
  } = useFilePreviewViewState(
    JSON.stringify([activeTab.key, levelKey, directory.activeLevel?.key]),
  );
  const { tabId, panelId, panelProps } = useFilePreviewIds(activeTab.key);
  // 编辑态跟着实际渲染的页签走，理由见 `switchTab`。
  // Editing follows the tab that actually renders; see `switchTab`.
  const editing = useFilePreviewEditing(
    JSON.stringify([activeTab.key, levelKey, directory.activeLevel?.key]),
  );
  const actions = useFilePreviewActions();

  const tabText = mergeLabels<typeof text>(text, activeTab.labels);
  const fileTab = isFileTab(activeTab) ? activeTab : undefined;
  const directoryTab: FilePreviewDirectoryTab | undefined =
    activeTab.kind === 'directory' ? activeTab : undefined;
  const contentTab: FilePreviewContentTab | undefined =
    fileTab || directoryTab ? undefined : (activeTab as FilePreviewContentTab);
  // 文件页签恒显示 URI 行——路径、复制、视图切换与编辑都在那一行。

  const switchTab = useCallback(
    (key: string) => {
      if (editing.saving) return false;
      // 只有「请求的 key 就是目标」才算已在该页签。当前页签被移除时 `currentKey`
      // 仍指向它、`activeTab` 已回落到 `tabs[0]`，此时点 `tabs[0]` 必须放行让宿主
      // 同步 `activeKey`，否则正文显示 B 而页签栏无选中项，无法修正。
      // Only "requested key is the target" counts as already there; a stale
      // `currentKey` must not block selecting the tab that actually renders.
      if (key === currentKey && key === activeTab.key) return false;
      const target = tabs.find((tab) => tab.key === key);
      if (!target || target.disabled) return false;
      // 请求的 key 过期、但目标就是当前实际渲染的页签时，只是把宿主的
      // `activeKey` 同步到有效值，并没有离开这个页签——不能丢弃它的编辑态，
      // 否则用户在 B 上编辑、点一下已选中的 B 就会丢掉草稿。
      //
      // When the requested key is stale but the target is the tab that already
      // renders, this only syncs the host's `activeKey`; the user never left the
      // tab, so its editing state must survive — otherwise editing B and clicking
      // the already-selected B would drop the draft.
      const syncingOnly = target.key === activeTab.key;
      if (!syncingOnly) {
        editing.discard(activeTab);
        setViewMode('preview');
        // 视图模式属于上一个页签的格式，切换后等新格式上报再决定是否显示切换。
        setViewModes([]);
        viewerHandle.current = null;
      }
      if (activeKey === undefined) setInternalKey(key);
      onActiveKeyChange?.(key);
      return true;
    },
    [
      activeKey,
      activeTab,
      currentKey,
      editing,
      onActiveKeyChange,
      tabs,
      setViewMode,
      setViewModes,
      viewerHandle,
    ],
  );

  useImperativeHandle(
    ref,
    () => ({
      setActiveTab: switchTab,
      getActiveTab: () => activeTab.key,
      getViewerHandle: () => (fileTab ? viewerHandle.current : null),
    }),
    [activeTab.key, fileTab, switchTab, viewerHandle],
  );

  // 只有该格式同时声明了预览与源码才可切换：图片、音视频、PDF 没有源码视图。
  // Switchable only when the adapter declares both; images, media and PDFs have
  // no source view.
  const canSwitchView =
    Boolean(fileTab) &&
    viewModes.includes('preview') &&
    viewModes.includes('source');

  // Markdown 正文里的链接：把原始目标交给宿主的 `openUri`，由它决定怎么解析
  // 与跳转。组件不做相对路径补全——那需要知道当前文档的地址，属于宿主。
  //
  // Links inside a Markdown body: hand the raw target to the host's `openUri` and
  // let it resolve and navigate. The component does not resolve relative paths —
  // that needs the current document's location, which only the host knows.
  const onLinkClick = useCallback<
    NonNullable<MarkdownAdapterOptions['onLinkClick']>
  >(
    (_event, link) => {
      if (fileTab) void actions.openUri(fileTab, link.url);
    },
    [actions, fileTab],
  );
  // 只有宿主给了 `openUri` 才接管导航：传入回调后底座会阻止默认跳转，宿主
  // 没提供能力时接管等于把链接点死。
  //
  // Hand navigation over only when the host supplies `openUri`: passing the
  // callback makes the base skip the default jump, so taking over without a
  // handler would leave links dead.
  const linkClickHandler = fileTab?.operations?.openUri
    ? onLinkClick
    : undefined;
  // 目录页签的当前层级由外壳持有，复制正文、复制路径与正文因此取同一份来源。
  // The shell owns the active level, so copy-content, copy-path and the body read the
  // same source.
  const level = directory;
  // 目录与文件都有路径行，复制路径按钮按路径出现，不按页签类型。
  // Both a directory and a file have a path row, so copy-path follows the path
  // rather than the tab kind.
  const currentPath = level.source?.path ?? fileTab?.source.path;
  // 目录的正文取当前那一级：复制正文要复制用户正在看的那一份。
  // A directory's body is its active level: copy-content copies what is on screen.
  const currentSource = level.source ?? fileTab?.source;

  const actionContext = useFilePreviewActionsContext({
    activeTab,
    fileTab,
    currentPath,
    currentSource,
    viewMode,
    canSwitchView,
    setViewMode,
    editing,
    operations: actions,
  });

  return (
    <div
      {...rest}
      className={['ov-file-preview', className].filter(Boolean).join(' ')}
    >
      <FilePreviewTabRow
        tabs={tabs}
        activeKey={activeTab.key}
        disabled={editing.saving}
        tabId={tabId}
        panelId={panelId}

        onSelect={(key) => void switchTab(key)}
      />
      <FilePreviewPanels
        contentTab={contentTab}
        directoryTab={level.directoryTab}
        fileTab={fileTab}
        panelProps={panelProps}
        panelClass="ov-file-preview__panel"
        levelKey={levelKey}
        onLevelChange={(key) => {
          if (editing.saving || editing.loadingEdit || key === levelKey) return;
          // Controlled requests may be declined; reset only when the rendered level changes.
          if (fileTab?.activeLevelKey === undefined) editing.discard(activeTab);
          selectLevel(key);
        }}
        text={tabText}
        locale={locale}
        actions={actions}
        actionContext={actionContext}
        editing={editing}
        viewMode={viewMode}
        setViewMode={setViewMode}
        onHandleChange={onHandleChange}
        onViewModesChange={onViewModesChange}
        linkClickHandler={linkClickHandler}
        activeTab={activeTab}
      />
    </div>
  );
});
