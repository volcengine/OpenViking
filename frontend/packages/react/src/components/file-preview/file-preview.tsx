import type {
  ViewerHandle,
  ViewerViewModeOption,
} from '@ark-design/ui/file-viewer/core';
import {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';

import type { MarkdownAdapterOptions } from '@ark-design/ui/file-viewer/adapters/markdown';

import { FilePreviewBody } from './file-preview-body.js';
import { FilePreviewHeader } from './file-preview-header.js';
import { DEFAULT_LABELS } from './file-preview-labels.js';
import { TruncatedNotice } from './file-preview-state.js';
import { FilePreviewTabRow } from './file-preview-tab-row.js';
import { useFilePreviewActions } from './use-file-preview-actions.js';
import { useFilePreviewActionsContext } from './use-file-preview-actions-context.js';
import { useFilePreviewEditing } from './use-file-preview-editing.js';
import type {
  FilePreviewComponent,
  FilePreviewContentTab,
  FilePreviewFileTab,
  FilePreviewHandle,
  FilePreviewProps,
  FilePreviewTab,
  FilePreviewViewMode,
} from './types.js';

export type {
  FilePreviewActions,
  FilePreviewComponent,
  FilePreviewContentTab,
  FilePreviewEditing,
  FilePreviewFileTab,
  FilePreviewFileTabSlots,
  FilePreviewHandle,
  FilePreviewHeaderSlots,
  FilePreviewLabels,
  FilePreviewOperations,
  FilePreviewProps,
  FilePreviewSource,
  FilePreviewTab,
  FilePreviewTabBase,
  FilePreviewViewMode,
} from './types.js';

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
  const text = { ...DEFAULT_LABELS, ...labels };
  const [internalKey, setInternalKey] = useState(
    () => defaultActiveKey ?? tabs[0].key,
  );
  const [viewMode, setViewMode] = useState<FilePreviewViewMode>('preview');
  const [viewModes, setViewModes] = useState<readonly string[]>([]);
  const viewerHandle = useRef<ViewerHandle | null>(null);
  const editing = useFilePreviewEditing();
  const actions = useFilePreviewActions();

  const currentKey = activeKey ?? internalKey;
  const activeTab = tabs.find((tab) => tab.key === currentKey) ?? tabs[0];
  const tabText = { ...text, ...activeTab.labels };
  const fileTab = isFileTab(activeTab) ? activeTab : undefined;
  const contentTab: FilePreviewContentTab | undefined = fileTab
    ? undefined
    : (activeTab as FilePreviewContentTab);
  // 文件页签恒显示 URI 行——路径、复制、视图切换与编辑都在那一行。

  const switchTab = useCallback(
    (key: string) => {
      if (key === currentKey || editing.saving) return false;
      const target = tabs.find((tab) => tab.key === key);
      if (!target || target.disabled) return false;
      editing.discard(activeTab);
      setViewMode('preview');
      // 视图模式属于上一个页签的格式，切换后等新格式上报再决定是否显示切换。
      setViewModes([]);
      viewerHandle.current = null;
      if (activeKey === undefined) setInternalKey(key);
      onActiveKeyChange?.(key);
      return true;
    },
    [activeKey, activeTab, currentKey, editing, onActiveKeyChange, tabs],
  );

  const onHandleChange = useCallback((handle: ViewerHandle | null) => {
    viewerHandle.current = handle;
  }, []);

  const onViewModesChange = useCallback(
    (modes: readonly ViewerViewModeOption[]) => {
      // 内容没变就保留原引用：每次写入新数组都会触发一次重渲染，而适配器
      // 可能在每次渲染后再次上报，形成反复更新。
      const next = modes.map((mode) => mode.value);
      setViewModes((previous) =>
        previous.join() === next.join() ? previous : next,
      );
    },
    [],
  );

  useImperativeHandle(
    ref,
    () => ({
      setActiveTab: switchTab,
      getActiveTab: () => currentKey,
      getViewerHandle: () => (fileTab ? viewerHandle.current : null),
    }),
    [currentKey, fileTab, switchTab],
  );

  // 只在该格式确实同时支持预览与源码时认为可切换：图片、音视频、PDF 等没有
  // 源码视图，Adapter 不会声明这两个模式。
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
  const actionContext = useFilePreviewActionsContext({
    activeTab,
    fileTab,
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
        activeKey={currentKey}
        disabled={editing.saving}
        onSelect={(key) => void switchTab(key)}
      />
      {contentTab ? (
        // 内容页签整块交给宿主：组件只画页签行，正文不套任何容器，也没有
        // 标题行——组件不知道该在那里显示什么，套一层只会限制宿主的排版。
        <div
          id={`ov-file-preview-panel-${currentKey}`}
          role="tabpanel"
          aria-labelledby={`ov-file-preview-tab-${currentKey}`}
          className="ov-file-preview__panel ov-file-preview__panel--content"
        >
          {contentTab.content}
        </div>
      ) : fileTab ? (
        <div
          id={`ov-file-preview-panel-${currentKey}`}
          role="tabpanel"
          aria-labelledby={`ov-file-preview-tab-${currentKey}`}
          className="ov-file-preview__panel"
        >
          <FilePreviewHeader
            fileTab={fileTab}
            actions={actionContext}
            text={tabText}
            onOpenParent={(uri) => void actions.openParent(activeTab, uri)}
          />
          <div className="ov-file-preview__body">
            <FilePreviewBody
              fileTab={fileTab}
              text={tabText}
              locale={locale}
              viewMode={viewMode}
              editing={editing}
              onViewModeChange={setViewMode}
              onHandleChange={onHandleChange}
              onViewModesChange={onViewModesChange}
              onDownload={() => void actions.download(fileTab)}
              onLinkClick={linkClickHandler}
            />
          </div>
          {fileTab.source.truncated ? <TruncatedNotice text={tabText} /> : null}
          {fileTab.slots?.footer ? (
            <div className="ov-file-preview__footer">
              {fileTab.slots.footer}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
});
