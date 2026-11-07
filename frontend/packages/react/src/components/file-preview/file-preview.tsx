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

import { FilePreviewBody } from './file-preview-body.js';
import { FilePreviewHeader } from './file-preview-header.js';
import { DEFAULT_LABELS } from './file-preview-labels.js';
import { readCopyableText } from './file-preview-source.js';
import { TruncatedNotice } from './file-preview-state.js';
import { FilePreviewTabRow } from './file-preview-tab-row.js';
import { useFilePreviewActions } from './use-file-preview-actions.js';
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
      setViewModes(modes.map((mode) => mode.value));
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

  const canEdit = Boolean(fileTab?.editing) && !editing.editing;
  // 只在该格式确实同时支持预览与源码时显示切换：图片、音视频、PDF 等没有
  // 源码视图，Adapter 不会声明这两个模式。
  const showViewModeSwitch =
    Boolean(fileTab) &&
    activeTab.showViewModeSwitch !== false &&
    viewModes.includes('preview') &&
    viewModes.includes('source');

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
      <div
        id={`ov-file-preview-panel-${currentKey}`}
        role="tabpanel"
        aria-labelledby={`ov-file-preview-tab-${currentKey}`}
        className="ov-file-preview__panel"
      >
        <FilePreviewHeader
          activeTab={activeTab}
          fileTab={fileTab}
          text={tabText}
          viewMode={viewMode}
          showViewModeSwitch={showViewModeSwitch}
          editing={editing.editing}
          saving={editing.saving}
          canEdit={canEdit}
          onViewModeChange={setViewMode}
          onOpenParent={(uri) => void actions.openParent(activeTab, uri)}
          onCopyPath={() =>
            fileTab?.source.path
              ? void actions.copyPath(activeTab, fileTab.source.path)
              : undefined
          }
          onCopyContent={() => {
            const copyable = fileTab && readCopyableText(fileTab.source);
            if (fileTab && copyable)
              void actions.copyContent(fileTab, copyable);
          }}
          onDownload={() => fileTab && void actions.download(fileTab)}
          onEditStart={() => {
            if (!fileTab) return;
            setViewMode('source');
            void editing.start(fileTab);
          }}
          onEditSave={() => {
            if (!fileTab) return;
            void editing.save(fileTab).then((saved) => {
              // 保存成功后回到预览视图，与进入编辑前的视图状态对齐。
              if (saved) setViewMode('preview');
            });
          }}
          onEditCancel={() => editing.discard(activeTab)}
        />
        <div className="ov-file-preview__body">
          <FilePreviewBody
            fileTab={fileTab}
            contentTab={contentTab}
            text={tabText}
            locale={locale}
            viewMode={viewMode}
            editing={editing}
            onViewModeChange={setViewMode}
            onHandleChange={onHandleChange}
            onViewModesChange={onViewModesChange}
            onDownload={() => fileTab && void actions.download(fileTab)}
          />
        </div>
        {fileTab?.source.truncated ? <TruncatedNotice text={tabText} /> : null}
        {activeTab.slots?.footer ? (
          <div className="ov-file-preview__footer">
            {activeTab.slots.footer}
          </div>
        ) : null}
      </div>
    </div>
  );
});
