import {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useMemo,
  useState,
} from 'react';
import type { MouseEvent, ReactNode } from 'react';

import styles from './file-preview.module.css';
import {
  type FilePreviewComponent,
  type FilePreviewContentTab,
  type FilePreviewFileTab,
  type FilePreviewHandle,
  type FilePreviewLabels,
  type FilePreviewProps,
  type FilePreviewTab,
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

const DEFAULT_LABELS: Required<
  Pick<
    FilePreviewLabels,
    | 'copyContent'
    | 'copyContentSuccess'
    | 'copyContentEmpty'
    | 'copyPath'
    | 'copyPathSuccess'
    | 'openParent'
    | 'edit'
    | 'save'
    | 'cancel'
    | 'preview'
    | 'source'
    | 'retry'
    | 'loadFailed'
    | 'saveFailed'
    | 'unsupported'
  >
> = {
  copyContent: 'Copy content',
  copyContentSuccess: 'Content copied',
  copyContentEmpty: 'Nothing to copy',
  copyPath: 'Copy path',
  copyPathSuccess: 'Path copied',
  openParent: 'Open parent directory',
  edit: 'Edit',
  save: 'Save',
  cancel: 'Cancel',
  preview: 'Preview',
  source: 'Source',
  retry: 'Retry',
  loadFailed: 'Failed to load the file',
  saveFailed: 'Failed to save',
  unsupported: 'Preview is not supported for this format',
};

function isFileTab(tab: FilePreviewTab): tab is FilePreviewFileTab {
  return tab.kind === 'file';
}

/** 从文件页签的 `source` 里取出可复制的纯文本；拿不到时返回 `undefined`。 */
function readCopyableText(tab: FilePreviewFileTab): string | undefined {
  const { content } = tab.source;
  return typeof content === 'string' && content.length > 0
    ? content
    : undefined;
}

function FileIcon() {
  return (
    <svg
      aria-hidden="true"
      className={styles.icon}
      viewBox="0 0 16 16"
      width="14"
      height="14"
    >
      <path
        d="M4 1.5h5L12.5 5v9.5h-8.5V1.5Z"
        fill="none"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="1.2"
      />
      <path
        d="M9 1.5V5h3.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
      />
    </svg>
  );
}

function CopyIcon() {
  return (
    <svg
      aria-hidden="true"
      className={styles.actionIcon}
      viewBox="0 0 16 16"
      width="12"
      height="12"
    >
      <rect
        x="5.5"
        y="5.5"
        width="8"
        height="9"
        rx="1.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
      />
      <path
        d="M10.5 5.5v-2a1 1 0 0 0-1-1h-7a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
      />
    </svg>
  );
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

  const activeTab =
    tabs.find((tab) => tab.key === activeKey) ?? (tabs[0] as FilePreviewTab);

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

  const tabText = { ...text, ...activeTab.labels };

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
  const contentTab = !isFileTab(activeTab)
    ? (activeTab as FilePreviewContentTab)
    : undefined;

  const headerActions = activeTab.slots?.actions;
  const titleExtra = activeTab.slots?.titleExtra;

  const body = useMemo<ReactNode>(() => {
    if (contentTab) return contentTab.content;

    const tab = fileTab as FilePreviewFileTab;
    const { source } = tab;
    const raw = source.content;

    if (editing && tab.editing) {
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
          {tab.slots?.unsupportedFallback ?? (
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
        {viewMode === 'source' ? (
          <pre className={styles.pre}>{raw}</pre>
        ) : (
          <pre className={styles.pre}>{raw}</pre>
        )}
      </div>
    );
  }, [contentTab, draft, editing, fileTab, text.unsupported, viewMode]);

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

      <div className={styles.header}>
        <div className={styles.pathRow}>
          {fileTab?.source.path ? (
            <button
              type="button"
              className={styles.breadcrumb}
              onClick={(event: MouseEvent<HTMLButtonElement>) => {
                event.stopPropagation();
                fileTab.onOpenParent?.(fileTab.source.path as string);
              }}
              disabled={!fileTab.onOpenParent}
              title={fileTab.source.path}
            >
              {fileTab.source.path}
            </button>
          ) : (
            <span className={styles.breadcrumb}>{activeTab.key}</span>
          )}

          {fileTab && fileTab.onCopyPath && fileTab.source.path ? (
            <button
              type="button"
              className={styles.action}
              aria-label={tabText.copyPath}
              title={tabText.copyPath}
              onClick={() => void copy(fileTab.source.path)}
            >
              <CopyIcon />
            </button>
          ) : null}

          {titleExtra ? (
            <span className={styles.titleExtra}>{titleExtra}</span>
          ) : null}
        </div>

        <div className={styles.headerActions}>
          {fileTab && fileTab.showViewModeSwitch !== false ? (
            <span className={styles.switch}>
              <button
                type="button"
                className={styles.switchItem}
                data-active={viewMode === 'preview' || undefined}
                onClick={() => setViewMode('preview')}
              >
                {tabText.preview}
              </button>
              <button
                type="button"
                className={styles.switchItem}
                data-active={viewMode === 'source' || undefined}
                onClick={() => setViewMode('source')}
              >
                {tabText.source}
              </button>
            </span>
          ) : null}

          {fileTab?.onCopyContent ? (
            <button
              type="button"
              className={styles.action}
              aria-label={tabText.copyContent}
              title={
                readCopyableText(fileTab)
                  ? tabText.copyContent
                  : tabText.copyContentEmpty
              }
              disabled={!readCopyableText(fileTab)}
              onClick={() => void copy(readCopyableText(fileTab))}
            >
              <CopyIcon />
            </button>
          ) : null}

          {fileTab?.editing && !editing ? (
            <button
              type="button"
              className={styles.button}
              onClick={() => {
                const next = fileTab.source.content ?? '';
                setDraft(next);
                setEditing(true);
              }}
            >
              {tabText.edit}
            </button>
          ) : null}

          {fileTab?.editing && editing ? (
            <>
              <button
                type="button"
                className={styles.button}
                onClick={() => {
                  setEditing(false);
                  fileTab.editing?.onCancel?.(draft);
                }}
              >
                {tabText.cancel}
              </button>
              <button
                type="button"
                className={styles.buttonPrimary}
                onClick={() => {
                  void fileTab.editing?.onSave(draft);
                  setEditing(false);
                }}
              >
                {tabText.save}
              </button>
            </>
          ) : null}

          {fileTab?.onDownload ? (
            <button
              type="button"
              className={styles.button}
              onClick={() => fileTab.onDownload?.()}
            >
              Download
            </button>
          ) : null}

          {headerActions ? (
            <span className={styles.extraActions}>{headerActions}</span>
          ) : null}
        </div>
      </div>

      <div className={styles.body}>{body}</div>
    </div>
  );
});
