import type { MouseEvent } from 'react';

import { CopyIcon } from './file-preview-icons.js';
import { readCopyableText } from './file-preview-labels.js';
import { performPreviewOperation } from './file-preview-operations.js';
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
  loadingEdit: boolean;
  saving: boolean;
  onViewModeChange: (mode: 'preview' | 'source') => void;
  onEditStart: () => void;
  onEditCancel: () => void;
  onEditSave: () => void;
}

async function copyText(value: string, onCopied?: (text: string) => void) {
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    return;
  }
  onCopied?.(value);
}

/** 内容区标题行：路径面包屑、复制、视图切换、编辑与页签级插槽。 */
export function FilePreviewHeader({
  activeTab,
  fileTab,
  text,
  viewMode,
  editing,
  loadingEdit,
  saving,
  onViewModeChange,
  onEditStart,
  onEditCancel,
  onEditSave,
}: FilePreviewHeaderProps) {
  const titleExtra = activeTab.slots?.titleExtra;
  const headerActions = activeTab.slots?.actions;
  const path = fileTab?.source.path;

  return (
    <div className="ov-file-preview__header">
      <div className="ov-file-preview__path-row">
        {path ? (
          <button
            type="button"
            className="ov-file-preview__breadcrumb"
            onClick={(event: MouseEvent<HTMLButtonElement>) => {
              event.stopPropagation();
              void performPreviewOperation(
                fileTab.operations?.openParent
                  ? () => fileTab.operations!.openParent!(path)
                  : undefined,
                () => fileTab.onOpenParent?.(path),
              );
            }}
            disabled={!fileTab.operations?.openParent || saving}
            title={path}
          >
            {path}
          </button>
        ) : (
          <span className="ov-file-preview__breadcrumb">{activeTab.key}</span>
        )}

        {path ? (
          <button
            type="button"
            className="ov-file-preview__action"
            aria-label={text.copyPath}
            title={text.copyPath}
            onClick={() => void copyText(path, fileTab.onCopyPath)}
          >
            <CopyIcon />
          </button>
        ) : null}

        {titleExtra ? (
          <span className="ov-file-preview__title-extra">{titleExtra}</span>
        ) : null}
      </div>

      <div className="ov-file-preview__header-actions">
        {fileTab && fileTab.showViewModeSwitch !== false ? (
          <span className="ov-file-preview__switch">
            <button
              type="button"
              className="ov-file-preview__switch-item"
              data-active={viewMode === 'preview' || undefined}
              onClick={() => onViewModeChange('preview')}
            >
              {text.preview}
            </button>
            <button
              type="button"
              className="ov-file-preview__switch-item"
              data-active={viewMode === 'source' || undefined}
              onClick={() => onViewModeChange('source')}
            >
              {text.source}
            </button>
          </span>
        ) : null}

        {fileTab ? (
          <button
            type="button"
            className="ov-file-preview__action"
            aria-label={text.copyContent}
            title={
              readCopyableText(fileTab)
                ? text.copyContent
                : text.copyContentEmpty
            }
            disabled={!readCopyableText(fileTab)}
            onClick={() => {
              const value = readCopyableText(fileTab);
              if (value) void copyText(value, fileTab.onCopyContent);
            }}
          >
            <CopyIcon />
          </button>
        ) : null}

        {fileTab?.editing && !editing ? (
          <button
            type="button"
            className="ov-file-preview__button"
            disabled={
              loadingEdit ||
              (!fileTab.editing.loadContent &&
                fileTab.source.content === undefined &&
                !fileTab.source.file)
            }
            onClick={onEditStart}
          >
            {text.edit}
          </button>
        ) : null}

        {fileTab?.editing && editing ? (
          <>
            <button
              type="button"
              className="ov-file-preview__button"
              onClick={onEditCancel}
              disabled={saving}
            >
              {text.cancel}
            </button>
            <button
              type="button"
              className="ov-file-preview__button--primary"
              onClick={onEditSave}
              disabled={saving}
            >
              {text.save}
            </button>
          </>
        ) : null}

        {fileTab?.operations?.download ? (
          <button
            type="button"
            className="ov-file-preview__button"
            onClick={() => {
              void performPreviewOperation(
                fileTab.operations?.download,
                fileTab.onDownload,
              );
            }}
          >
            {text.download}
          </button>
        ) : null}

        {headerActions ? (
          <span className="ov-file-preview__extra-actions">
            {headerActions}
          </span>
        ) : null}
      </div>
    </div>
  );
}
