import { forwardRef, useCallback, useImperativeHandle, useState } from 'react';

import { FilePreviewBody } from './file-preview-body.js';
import { FilePreviewHeader } from './file-preview-header.js';
import { FileIcon } from './file-preview-icons.js';
import { DEFAULT_LABELS } from './file-preview-labels.js';
import styles from './file-preview.module.css';
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
  FilePreviewProps,
  FilePreviewSource,
  FilePreviewTab,
  FilePreviewTabBase,
} from './types.js';

function isFileTab(tab: FilePreviewTab): tab is FilePreviewFileTab {
  return tab.kind === 'file';
}

/** 进入编辑时向宿主取正文的结果。 */
type EditRequest =
  | { status: 'ready'; text: string }
  | { status: 'abandoned' }
  | { status: 'failed' };

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
  const [editing, setEditing] = useState(false);
  const [loadingEdit, setLoadingEdit] = useState(false);
  const [editFailed, setEditFailed] = useState(false);
  const [draft, setDraft] = useState('');

  const activeTab = tabs.find((tab) => tab.key === activeKey) ?? tabs[0];
  const tabText = { ...text, ...activeTab.labels };

  const fileTab = isFileTab(activeTab) ? activeTab : undefined;
  const contentTab = !isFileTab(activeTab) ? activeTab : undefined;

  // 退出编辑并丢弃草稿：通知原页签，再清空编辑态。切换页签与取消共用。
  const discardDraft = useCallback(
    (tab: FilePreviewTab | undefined, value: string) => {
      if (tab && isFileTab(tab)) tab.editing?.onCancel?.(value);
      setEditing(false);
      setLoadingEdit(false);
      setDraft('');
    },
    [],
  );

  const switchTab = useCallback(
    (key: string) => {
      if (key === activeKey) return true;
      if (editing || loadingEdit) discardDraft(activeTab, draft);
      setEditFailed(false);
      setActiveKey(key);
      setViewMode('preview');
      return true;
    },
    [activeKey, activeTab, discardDraft, draft, editing, loadingEdit],
  );

  useImperativeHandle(
    ref,
    () => ({
      setActiveTab: (key) => {
        const target = tabs.find((tab) => tab.key === key);
        if (!target || target.disabled) return false;
        return switchTab(key);
      },
      getActiveTab: () => activeKey,
    }),
    [activeKey, switchTab, tabs],
  );

  // 进入编辑：有 onRequestEdit 时以它的返回值为正文，宿主自行处理取数。
  const startEdit = useCallback(async (tab: FilePreviewFileTab) => {
    setEditFailed(false);
    const request = tab.editing?.onRequestEdit;
    if (!request) {
      setDraft(tab.source.content ?? '');
      setEditing(true);
      return;
    }

    setLoadingEdit(true);
    let result: EditRequest;
    try {
      const returned = await request(tab.source);
      result =
        returned === undefined
          ? { status: 'abandoned' }
          : { status: 'ready', text: returned };
    } catch {
      result = { status: 'failed' };
    }
    setLoadingEdit(false);

    if (result.status === 'ready') {
      setDraft(result.text);
      setEditing(true);
    } else if (result.status === 'failed') {
      setEditFailed(true);
    }
  }, []);

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
            disabled={tab.disabled}
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
        editing={editing}
        onViewModeChange={setViewMode}
        onEditStart={() => {
          if (fileTab) void startEdit(fileTab);
        }}
        onEditCancel={() => discardDraft(activeTab, draft)}
        onEditSave={() => {
          void fileTab?.editing?.onSave(draft);
          setEditing(false);
          setDraft('');
        }}
      />

      <div className={styles.body}>
        <FilePreviewBody
          fileTab={fileTab}
          contentTab={contentTab}
          text={tabText}
          editing={editing}
          loadingEdit={loadingEdit}
          editFailed={editFailed}
          draft={draft}
          onDraftChange={setDraft}
        />
      </div>
    </div>
  );
});
