import type {
  ViewerHandle,
  ViewerViewModeOption,
} from '@ark-design/ui/file-viewer/core';
import type { MarkdownAdapterOptions } from '@ark-design/ui/file-viewer/adapters/markdown';

import { FilePreviewEditor } from './file-preview-editor.js';
import { isEmptyFile } from './file-preview-source.js';
import { EmptyFileState } from './file-preview-state.js';
import { LazyFilePreviewViewer } from './file-preview-lazy-viewer.js';
import type { useFilePreviewEditing } from './use-file-preview-editing.js';
import type {
  FilePreviewFileTab,
  FilePreviewLabels,
  FilePreviewViewMode,
} from './types.js';

export interface FilePreviewBodyProps {
  fileTab: FilePreviewFileTab;
  text: Required<FilePreviewLabels>;
  locale?: 'en-US' | 'zh-CN';
  viewMode: FilePreviewViewMode;
  editing: ReturnType<typeof useFilePreviewEditing>;
  onViewModeChange: (mode: FilePreviewViewMode) => void;
  onHandleChange: (handle: ViewerHandle | null) => void;
  onViewModesChange: (modes: readonly ViewerViewModeOption[]) => void;
  onDownload: () => void;
  /** Markdown 正文里的链接被点击时调用。 */
  onLinkClick?: MarkdownAdapterOptions['onLinkClick'];
}

/**
 * 文件页签的正文：按编辑态、空文件、底座依次短路。内容页签不走这里——它的
 * 正文整块由宿主渲染。
 *
 * The body of a file tab: it short-circuits through editing, empty file and then
 * the base component. A content tab never reaches this — its body is the host's.
 */
export function FilePreviewBody({
  fileTab,
  text,
  locale,
  viewMode,
  editing,
  onViewModeChange,
  onHandleChange,
  onViewModesChange,
  onDownload,
  onLinkClick,
}: FilePreviewBodyProps) {
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
      onLinkClick={onLinkClick}
    />
  );
}
