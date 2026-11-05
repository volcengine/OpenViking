import { forwardRef, useImperativeHandle, useState } from 'react';

import { FilePreviewBody } from './file-preview-body.js';
import { FilePreviewHeader } from './file-preview-header.js';
import { FileIcon } from './file-preview-icons.js';
import { DEFAULT_LABELS } from './file-preview-labels.js';
import styles from './file-preview.module.css';
import { useFilePreviewEditing } from './use-file-preview-editing.js';
import type {
  FilePreviewComponent,
  FilePreviewFileTab,
  FilePreviewHandle,
  FilePreviewProps,
  FilePreviewTab,
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
} from './types.js';

function isFileTab(tab: FilePreviewTab): tab is FilePreviewFileTab {
  return tab.kind === 'file';
}

/**
 * 文件预览的占位实现。
 *
 * 外壳（页签行、内容区标题行、页签切换与 ref 句柄）按最终形态实现，可直接
 * 交互；文件正文尚未接入 FileViewer，文件页签先以纯文本降级展示，内容页签
 * 按宿主给的节点渲染。
 *
 * Placeholder implementation of the file preview.
 *
 * The shell — tab row, content header row, tab switching and the ref handle — is
 * implemented as it will ship and is fully interactive. The file body is not
 * wired to FileViewer yet: a file tab falls back to plain text, while a content
 * tab renders the node the host supplies.
 */
export const FilePreview: FilePreviewComponent = forwardRef<
  FilePreviewHandle,
  FilePreviewProps
>(function FilePreview({ tabs, labels, className, ...rest }, ref) {
  const text = { ...DEFAULT_LABELS, ...labels };
  const [activeKey, setActiveKey] = useState(() => tabs[0].key);
  const [viewMode, setViewMode] = useState<'preview' | 'source'>('preview');
  const editor = useFilePreviewEditing();

  const activeTab = tabs.find((tab) => tab.key === activeKey) ?? tabs[0];
  const tabText = { ...text, ...activeTab.labels };

  const fileTab = isFileTab(activeTab) ? activeTab : undefined;
  const contentTab = !isFileTab(activeTab) ? activeTab : undefined;

  const switchTab = (key: string) => {
    if (key === activeKey) return true;
    if (editor.saving) return false;
    setActiveKey(key);
    setViewMode('preview');
    editor.discard(activeTab);
    return true;
  };

  useImperativeHandle(ref, () => ({
    setActiveTab: (key) => {
      const target = tabs.find((tab) => tab.key === key);
      if (!target || target.disabled) return false;
      return switchTab(key);
    },
    getActiveTab: () => activeKey,
  }));

  const handleTabClick = (tab: FilePreviewTab) => {
    if (tab.disabled) return;
    switchTab(tab.key);
  };

  return (
    <div
      {...rest}
      className={[styles.root, className].filter(Boolean).join(' ')}
    >
      <div role="tablist" className={styles.tabRow}>
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={tab.key === activeTab.key}
            disabled={tab.disabled || editor.saving}
            className={styles.tab}
            onClick={() => handleTabClick(tab)}
          >
            <span className={styles.iconSlot}>{tab.icon ?? <FileIcon />}</span>
            <span className={styles.tabTitle}>
              {tab.title ?? (isFileTab(tab) ? tab.source.fileName : tab.key)}
            </span>
          </button>
        ))}
      </div>

      <FilePreviewHeader
        activeTab={activeTab}
        fileTab={fileTab}
        text={tabText}
        viewMode={viewMode}
        editing={editor.editing}
        loadingEdit={editor.loadingEdit}
        saving={editor.saving}
        onViewModeChange={setViewMode}
        onEditStart={() => {
          if (fileTab) void editor.start(fileTab);
        }}
        onEditCancel={() => editor.discard(activeTab)}
        onEditSave={() => {
          if (fileTab) void editor.save(fileTab);
        }}
      />

      <div className={styles.body}>
        <FilePreviewBody
          fileTab={fileTab}
          contentTab={contentTab}
          text={tabText}
          editing={editor.editing}
          loadingEdit={editor.loadingEdit}
          editFailed={editor.editFailed}
          saving={editor.saving}
          saveFailed={editor.saveFailed}
          draft={editor.draft}
          onDraftChange={editor.updateDraft}
        />
      </div>
    </div>
  );
});
