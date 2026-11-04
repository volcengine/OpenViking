import {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useMemo,
  useState,
} from 'react';
import type { ReactNode } from 'react';

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
  const [draft, setDraft] = useState('');

  const activeTab = tabs.find((tab) => tab.key === activeKey) ?? tabs[0];
  const tabText = { ...text, ...activeTab.labels };

  useImperativeHandle(
    ref,
    () => ({
      setActiveTab: (key) => {
        const target = tabs.find((tab) => tab.key === key);
        if (!target || target.disabled) return false;
        setActiveKey(key);
        return true;
      },
      getActiveTab: () => activeKey,
    }),
    [activeKey, tabs],
  );

  const copy = useCallback(async (value: string | undefined) => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // 剪贴板不可用时静默失败，宿主若需要反馈可自行接管。
    }
  }, []);

  const handleTabClick = (tab: FilePreviewTab) => {
    if (tab.disabled) return;
    setActiveKey(tab.key);
    setViewMode('preview');
    setEditing(false);
  };

  const fileTab = isFileTab(activeTab) ? activeTab : undefined;
  const contentTab = !isFileTab(activeTab) ? activeTab : undefined;

  const body = useMemo<ReactNode>(() => {
    if (contentTab) return contentTab.content;
    if (!fileTab) return null;

    const { source } = fileTab;
    const raw = source.content;

    if (editing && fileTab.editing) {
      return (
        <div className={styles.editor}>
          <textarea
            className={styles.textarea}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
        </div>
      );
    }

    if (typeof raw !== 'string') {
      return (
        <div className={styles.state}>
          {fileTab.slots?.unsupportedFallback ?? (
            <span className={styles.muted}>
              {source.file || source.url
                ? 'Binary preview requires FileViewer'
                : text.unsupported}
            </span>
          )}
        </div>
      );
    }

    return (
      <div className={styles.textBody}>
        <pre className={styles.pre}>{raw}</pre>
      </div>
    );
  }, [contentTab, draft, editing, fileTab, text.unsupported]);

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
        draft={draft}
        onViewModeChange={setViewMode}
        onCopy={copy}
        onEditStart={() => {
          setDraft(fileTab?.source.content ?? '');
          setEditing(true);
        }}
        onEditCancel={() => {
          setEditing(false);
          fileTab?.editing?.onCancel?.(draft);
        }}
        onEditSave={() => {
          void fileTab?.editing?.onSave(draft);
          setEditing(false);
        }}
      />

      <div className={styles.body}>{body}</div>
    </div>
  );
});
