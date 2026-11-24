import { useRenderErrors } from './file-preview-labels.js';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { FilePreviewCommandProvider } from './file-preview-command-state.js';
import { mediaKind } from './file-preview-media.js';
import { useLocale } from '../../shared/locale-context.js';
import { forwardRef, useState } from 'react';

import { mergeLabels } from '../../shared/locale.js';
import { FilePreviewPanels } from './file-preview-panels.js';
import { useFilePreviewViewState } from './use-file-preview-view-state.js';
import { useLinkClickHandler } from './use-link-click-handler.js';
import { useFilePreviewLevels } from './use-file-preview-levels.js';
import { resolveFilePreviewLabels } from './file-preview-labels.js';
import { FilePreviewTabRow } from './file-preview-tab-row.js';
import { useFilePreviewActions } from './use-file-preview-actions.js';
import { useFilePreviewActionsContext } from './use-file-preview-actions-context.js';
import { useFilePreviewIds } from './use-file-preview-ids.js';
import { useDirectoryLevel } from './use-directory-level.js';
import { useFilePreviewEditing } from './use-file-preview-editing.js';
import { useFilePreviewLeave } from './use-file-preview-leave.js';
import { useFilePreviewNavigation } from './use-file-preview-navigation.js';
import type {
  FilePreviewComponent,
  FilePreviewContentTab,
  FilePreviewHandle,
  FilePreviewProps,
} from './types.js';

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
export const FilePreviewContent: FilePreviewComponent = forwardRef<
  FilePreviewHandle,
  FilePreviewProps
>(function FilePreview(
  {
    tabs,
    activeKey,
    defaultActiveKey,
    toolbar,
    locale: localeProp,
    labels,
    onActiveKeyChange,
    className,
    ...rest
  },
  ref,
) {
  const locale = useLocale(localeProp);
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
  const identity = JSON.stringify([
    activeTab.key,
    levelKey,
    directory.activeLevel?.key,
    activeTab.kind === 'file' ? mediaKind(activeTab.source) : undefined,
  ]);
  const {
    viewMode,
    setViewMode,
    viewModes,
    setViewModes,
    viewerHandle,
    onHandleChange,
    onViewModesChange,
  } = useFilePreviewViewState(identity);
  const { tabId, panelId, panelProps } = useFilePreviewIds(activeTab.key);
  // 编辑态跟着实际渲染的页签走，理由见 `switchTab`。
  // Editing follows the tab that actually renders; see `switchTab`.
  const editing = useFilePreviewEditing(identity);
  const actions = useFilePreviewActions();

  const tabText = mergeLabels<typeof text>(text, activeTab.labels);
  const leave = useFilePreviewLeave(
    identity,
    editing.dirty,
    editing.saving || editing.versionNoteOpen,
    tabText,
  );
  const fileTab = activeTab.kind === 'file' ? activeTab : undefined;
  const directoryTab = activeTab.kind === 'directory' ? activeTab : undefined;
  const contentTab: FilePreviewContentTab | undefined =
    fileTab || directoryTab ? undefined : (activeTab as FilePreviewContentTab);
  // 文件页签恒显示 URI 行——路径、复制、视图切换与编辑都在那一行。

  const navigation = useFilePreviewNavigation(ref, {
    tabs,
    activeTab,
    currentKey,
    levelKey,
    selectLevel,
    editing,
    onSelect: activeKey === undefined ? setInternalKey : undefined,
    onActiveKeyChange,
    setViewMode,
    setViewModes,
    viewerHandle,
    confirmLeave: leave.confirm,
  });

  // 只有该格式同时声明了预览与源码才可切换：图片、音视频、PDF 没有源码视图。
  // Switchable only when the adapter declares both; images, media and PDFs have
  // no source view.
  const canSwitchView =
    Boolean(fileTab || directoryTab) &&
    !(fileTab && mediaKind(fileTab.source)) &&
    viewModes.includes('preview') &&
    viewModes.includes('source');

  // Markdown 正文里的链接：把原始目标交给宿主的 `openUri`，由它决定怎么解析
  // 与跳转。组件不做相对路径补全——那需要知道当前文档的地址，属于宿主。
  //
  // Links inside a Markdown body: hand the raw target to the host's `openUri` and
  // let it resolve and navigate. The component does not resolve relative paths —
  // that needs the current document's location, which only the host knows.
  // 文件链接由宿主的 `openUri` 接管；目录同文档锚点独立处理，其他目录链接
  // 按宿主是否提供 `openUri` 决定是否接管。
  //
  // File links use the host's `openUri`. Directory anchors are handled locally;
  // other directory links are taken over only when the host supplies `openUri`.
  const linkClickHandler = useLinkClickHandler(
    fileTab ?? directoryTab,
    actions,
    directory.activeLevel?.key,
  );
  // 目录页签的当前层级由外壳持有，复制正文、复制路径与正文因此取同一份来源。
  // The shell owns the active level, so copy-content, copy-path and the body read the
  // same source.
  // 目录与文件都有路径行，复制路径按钮按路径出现，不按页签类型。
  // Both a directory and a file have a path row, so copy-path follows the path
  // rather than the tab kind.
  const currentPath = directory.source?.path ?? fileTab?.source.path;
  // 目录的正文取当前那一级：复制正文要复制用户正在看的那一份。
  // A directory's body is its active level: copy-content copies what is on screen.
  const currentSource = directory.source ?? fileTab?.source;

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
    confirmLeave: leave.confirm,
  });

  return (
    <div
      {...rest}
      ref={leave.triggerRef}
      className={['ov-file-preview', className].filter(Boolean).join(' ')}
    >
      <FilePreviewTabRow
        tabs={tabs}
        activeKey={activeTab.key}
        disabled={editing.saving}
        editing={editing.loadingEdit || editing.editing || editing.editFailed}
        tabId={tabId}
        panelId={panelId}
        onSelect={navigation.selectTab}
      />
      <ErrorBoundary
        message={useRenderErrors(locale).content}
        locale={locale}
        resetKeys={[identity]}
      >
        <FilePreviewCommandProvider
          tab={selectedTab}
          locale={locale}
          text={tabText}
        >
          <FilePreviewPanels
            toolbar={toolbar}
            contentTab={contentTab}
            directoryTab={directory.directoryTab}
            fileTab={fileTab}
            panelProps={panelProps}
            panelClass="ov-file-preview__panel"
            levelKey={levelKey}
            onLevelChange={navigation.selectLevel}
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
        </FilePreviewCommandProvider>
      </ErrorBoundary>
      {leave.dialog}
    </div>
  );
});
