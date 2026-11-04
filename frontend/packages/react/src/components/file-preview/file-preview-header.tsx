import type { MouseEvent } from 'react';

import { CopyIcon } from './file-preview-icons.js';
import { readCopyableText } from './file-preview-labels.js';
import styles from './file-preview.module.css';
import type {
  FilePreviewFileTab,
  FilePreviewLabels,
  FilePreviewTab,
} from './types.js';

export interface FilePreviewHeaderProps {
  activeTab: FilePreviewTab;
  fileTab?: FilePreviewFileTab;
  text: Required<FilePreviewLabels>;
  viewMode: 'preview' | 'source';
  editing: boolean;
  onViewModeChange: (mode: 'preview' | 'source') => void;
  onEditStart: () => void;
  onEditCancel: () => void;
  onEditSave: () => void;
}

/** 内容区标题行：路径面包屑、复制、视图切换、编辑与页签级插槽。 */
export function FilePreviewHeader({
  activeTab,
  fileTab,
  text,
  viewMode,
  editing,
  onViewModeChange,
  onEditStart,
  onEditCancel,
  onEditSave,
}: FilePreviewHeaderProps) {
  const titleExtra = activeTab.slots?.titleExtra;
  const headerActions = activeTab.slots?.actions;
  const path = fileTab?.source.path;

  return (
    <div className={styles.header}>
      <div className={styles.pathRow}>
        {path ? (
          <button
            type="button"
            className={styles.breadcrumb}
            onClick={(event: MouseEvent<HTMLButtonElement>) => {
              event.stopPropagation();
              fileTab.onOpenParent?.(path);
            }}
            disabled={!fileTab.onOpenParent}
            title={path}
          >
            {path}
          </button>
        ) : (
          <span className={styles.breadcrumb}>{activeTab.key}</span>
        )}

        {fileTab?.onCopyPath && path ? (
          <button
            type="button"
            className={styles.action}
            aria-label={text.copyPath}
            title={text.copyPath}
            onClick={() => fileTab.onCopyPath?.(path)}
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
              onClick={() => onViewModeChange('preview')}
            >
              {text.preview}
            </button>
            <button
              type="button"
              className={styles.switchItem}
              data-active={viewMode === 'source' || undefined}
              onClick={() => onViewModeChange('source')}
            >
              {text.source}
            </button>
          </span>
        ) : null}

        {fileTab?.onCopyContent ? (
          <button
            type="button"
            className={styles.action}
            aria-label={text.copyContent}
            title={
              readCopyableText(fileTab)
                ? text.copyContent
                : text.copyContentEmpty
            }
            disabled={!readCopyableText(fileTab)}
            onClick={() => {
              const value = readCopyableText(fileTab);
              if (value) fileTab.onCopyContent?.(value);
            }}
          >
            <CopyIcon />
          </button>
        ) : null}

        {fileTab?.editing && !editing ? (
          <button type="button" className={styles.button} onClick={onEditStart}>
            {text.edit}
          </button>
        ) : null}

        {fileTab?.editing && editing ? (
          <>
            <button
              type="button"
              className={styles.button}
              onClick={onEditCancel}
            >
              {text.cancel}
            </button>
            <button
              type="button"
              className={styles.buttonPrimary}
              onClick={onEditSave}
            >
              {text.save}
            </button>
          </>
        ) : null}

        {fileTab?.onDownload ? (
          <button
            type="button"
            className={styles.button}
            onClick={() => fileTab.onDownload?.()}
          >
            {text.download}
          </button>
        ) : null}

        {headerActions ? (
          <span className={styles.extraActions}>{headerActions}</span>
        ) : null}
      </div>
    </div>
  );
}
