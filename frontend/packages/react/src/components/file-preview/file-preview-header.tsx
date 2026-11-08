import { FilePreviewBreadcrumb } from './file-preview-breadcrumb.js';
import {
  CopyIcon,
  DownloadIcon,
  IconButton,
} from './file-preview-header-icons.js';
import { getPathFileName, splitPathSegments } from './file-preview-path.js';
import { readCopyableText } from './file-preview-source.js';
import type {
  FilePreviewFileTab,
  FilePreviewLabels,
  FilePreviewTab,
  FilePreviewViewMode,
} from './types.js';

export interface FilePreviewHeaderProps {
  activeTab: FilePreviewTab;
  fileTab?: FilePreviewFileTab;
  text: Required<FilePreviewLabels>;
  viewMode: FilePreviewViewMode;
  showViewModeSwitch: boolean;
  editing: boolean;
  saving: boolean;
  canEdit: boolean;
  onViewModeChange: (mode: FilePreviewViewMode) => void;
  onOpenParent: (uri: string) => void;
  onCopyPath: () => void;
  onCopyContent: () => void;
  onDownload: () => void;
  onEditStart: () => void;
  onEditSave: () => void;
  onEditCancel: () => void;
}

/**
 * 内容区标题行：路径面包屑、复制、下载、视图切换、编辑与页签级插槽。
 * The content header: path breadcrumb, copy, download, view switch, editing and
 * per-tab slots.
 */
export function FilePreviewHeader({
  activeTab,
  fileTab,
  text,
  viewMode,
  showViewModeSwitch,
  editing,
  saving,
  canEdit,
  onViewModeChange,
  onOpenParent,
  onCopyPath,
  onCopyContent,
  onDownload,
  onEditStart,
  onEditSave,
  onEditCancel,
}: FilePreviewHeaderProps) {
  const path = fileTab?.source.path;
  const segments = splitPathSegments(path);
  const title =
    getPathFileName(path) ??
    (fileTab
      ? fileTab.source.fileName
      : typeof activeTab.title === 'string'
        ? activeTab.title
        : activeTab.key);
  const copyable = fileTab ? readCopyableText(fileTab.source) : undefined;
  const canOpenParent = Boolean(fileTab?.operations?.openParent);
  const canDownload = Boolean(fileTab?.operations?.download);

  return (
    <div className="ov-file-preview__header">
      <div className="ov-file-preview__path-row">
        <FilePreviewBreadcrumb
          segments={segments}
          title={title}
          canOpen={canOpenParent}
          disabled={saving}
          onOpen={onOpenParent}
        />
        {path ? (
          <IconButton
            label={text.copyPath}
            disabled={saving}
            onClick={onCopyPath}
          >
            <CopyIcon />
          </IconButton>
        ) : null}
        {activeTab.slots?.titleExtra}
      </div>
      <div className="ov-file-preview__header-actions">
        {activeTab.slots?.actions}
        {fileTab && !editing ? (
          <IconButton
            label={text.copyContent}
            disabled={saving || !copyable}
            outlined
            onClick={onCopyContent}
          >
            <CopyIcon />
          </IconButton>
        ) : null}
        {fileTab && canDownload && !editing ? (
          <IconButton
            label={text.download}
            disabled={saving}
            outlined
            onClick={onDownload}
          >
            <DownloadIcon />
          </IconButton>
        ) : null}
        {fileTab && showViewModeSwitch && !editing ? (
          <div className="ov-file-preview__switch" role="group">
            {(['preview', 'source'] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                className="ov-file-preview__switch-item"
                data-active={viewMode === mode || undefined}
                aria-pressed={viewMode === mode}
                disabled={saving}
                onClick={() => onViewModeChange(mode)}
              >
                {mode === 'preview' ? text.preview : text.source}
              </button>
            ))}
          </div>
        ) : null}
        {fileTab && canEdit && !editing ? (
          <button
            type="button"
            className="ov-file-preview__button"
            disabled={saving}
            onClick={onEditStart}
          >
            {text.edit}
          </button>
        ) : null}
        {fileTab && editing ? (
          <>
            <button
              type="button"
              className="ov-file-preview__button"
              disabled={saving}
              onClick={onEditCancel}
            >
              {text.cancel}
            </button>
            <button
              type="button"
              className="ov-file-preview__button ov-file-preview__button--primary"
              disabled={saving}
              onClick={onEditSave}
            >
              {text.save}
            </button>
          </>
        ) : null}
      </div>
    </div>
  );
}
