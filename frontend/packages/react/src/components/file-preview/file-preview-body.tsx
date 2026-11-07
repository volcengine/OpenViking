import type {
  ViewerHandle,
  ViewerViewModeOption,
} from '@ark-design/ui/file-viewer/core';

import { FilePreviewEditor } from './file-preview-editor.js';
import { isEmptyFile } from './file-preview-source.js';
import { EmptyFileState } from './file-preview-state.js';
import { LazyFilePreviewViewer } from './file-preview-lazy-viewer.js';
import type { useFilePreviewEditing } from './use-file-preview-editing.js';
import type {
  FilePreviewContentTab,
  FilePreviewFileTab,
  FilePreviewLabels,
  FilePreviewViewMode,
} from './types.js';

export interface FilePreviewBodyProps {
  fileTab?: FilePreviewFileTab;
  contentTab?: FilePreviewContentTab;
  text: Required<FilePreviewLabels>;
  locale?: 'en-US' | 'zh-CN';
  viewMode: FilePreviewViewMode;
  editing: ReturnType<typeof useFilePreviewEditing>;
  onViewModeChange: (mode: FilePreviewViewMode) => void;
  onHandleChange: (handle: ViewerHandle | null) => void;
  onViewModesChange: (modes: readonly ViewerViewModeOption[]) => void;
  onDownload: () => void;
}

/**
 * 正文分派：内容页签原样渲染，文件页签按编辑态、空文件、底座依次短路。
 * Body dispatch: a content tab renders as-is; a file tab short-circuits through
 * editing, empty file and then the base component.
 */
export function FilePreviewBody({
  fileTab,
  contentTab,
  text,
  locale,
  viewMode,
  editing,
  onViewModeChange,
  onHandleChange,
  onViewModesChange,
  onDownload,
}: FilePreviewBodyProps) {
  if (contentTab) return <>{contentTab.content}</>;
  if (!fileTab) return null;

  if (editing.editing || editing.loadingEdit) {
    return (
      <FilePreviewEditor
        draft={editing.draft}
        text={text}
        loading={editing.loadingEdit}
        failed={editing.editFailed}
        saveFailed={editing.saveFailed}
        saving={editing.saving}
        onChange={editing.updateDraft}
      />
    );
  }

  if (isEmptyFile(fileTab.source)) return <EmptyFileState text={text} />;

  return (
    <LazyFilePreviewViewer
      tab={fileTab}
      text={text}
      locale={locale}
      viewMode={viewMode}
      onViewModeChange={onViewModeChange}
      onDownload={onDownload}
      onHandleChange={onHandleChange}
      onViewModesChange={onViewModesChange}
    />
  );
}
