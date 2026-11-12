import type { Locale } from '../../shared/locale.js';
import type {
  ViewerHandle,
  ViewerViewModeOption,
} from '@ark-design/ui/file-viewer/core';
import type { MarkdownAdapterOptions } from '@ark-design/ui/file-viewer/adapters/markdown';

import { FilePreviewMedia, mediaKind } from './file-preview-media.js';
import { FilePreviewImageFrame } from './file-preview-image-frame.js';
import { FilePreviewEditor } from './file-preview-editor.js';
import { isEmptyFile } from './file-preview-source.js';
import { EmptyFileState, LoadingState } from './file-preview-state.js';
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
  locale?: Locale;
  viewMode: FilePreviewViewMode;
  editing: ReturnType<typeof useFilePreviewEditing>;
  onViewModeChange: (mode: FilePreviewViewMode) => void;
  onHandleChange: (handle: ViewerHandle | null) => void;
  onViewModesChange: (modes: readonly ViewerViewModeOption[]) => void;
  /** Markdown 正文里的链接被点击时调用。 */
  onLinkClick?: MarkdownAdapterOptions['onLinkClick'];
  /** 读取失败后退出编辑态。Leaves editing after a failed load. */
  onCancelEditing: () => void;
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
  onLinkClick,
  onCancelEditing,
}: FilePreviewBodyProps) {
  // `editFailed` 也要走编辑器分支：读取失败时 `editing` 与 `loadingEdit` 都是
  // false，只检查这两项会直接落回普通预览，用户看不到读取失败的提示。
  //
  // `editFailed` belongs here too: on a failed load both `editing` and
  // `loadingEdit` are false, so checking only those two falls back to the plain
  // preview and the failure message never shows.
  if (editing.editing || editing.loadingEdit || editing.editFailed) {
    return (
      <FilePreviewEditor
        draft={editing.draft}
        text={text}
        loading={editing.loadingEdit}
        failed={editing.editFailed}
        saveFailed={editing.saveFailed}
        saving={editing.saving}
        onChange={editing.updateDraft}
        onCancel={onCancelEditing}
      />
    );
  }

  // 加载中优先于空文件：`source` 还没填上，不能当成「文件为空」。
  // Loading outranks empty: `source` is not filled in yet, which is not an empty file.
  if (fileTab.loading)
    return fileTab.slots?.loadingFallback ?? <LoadingState text={text} />;

  if (isEmptyFile(fileTab.source)) return <EmptyFileState text={text} />;

  const media = mediaKind(fileTab.source);
  if (media)
    return (
      <FilePreviewMedia
        source={fileTab.source}
        kind={media}
        text={text}
        slots={fileTab.slots}
      />
    );

  return (
    <FilePreviewImageFrame
      key={JSON.stringify([
        fileTab.source.path,
        fileTab.source.fileName,
        fileTab.source.url,
      ])}
    >
      <LazyFilePreviewViewer
        tab={fileTab}
        text={text}
        locale={locale}
        viewMode={viewMode}
        onViewModeChange={onViewModeChange}
        onHandleChange={onHandleChange}
        onViewModesChange={onViewModesChange}
        onLinkClick={onLinkClick}
      />
    </FilePreviewImageFrame>
  );
}
