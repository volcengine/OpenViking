import type { MarkdownAdapterOptions } from '@ark-design/ui/file-viewer/adapters/markdown';
import type {
  ViewerHandle,
  ViewerViewModeOption,
} from '@ark-design/ui/file-viewer/core';
import type { HTMLAttributes } from 'react';

import { FilePreviewBody } from './file-preview-body.js';
import { FilePreviewDirectoryBody } from './file-preview-directory.js';
import { FilePreviewHeader } from './file-preview-header.js';
import { TruncatedNotice } from './file-preview-state.js';
import type { useFilePreviewActions } from './use-file-preview-actions.js';
import type { FilePreviewActions } from './types.js';
import type { useFilePreviewEditing } from './use-file-preview-editing.js';
import type {
  FilePreviewContentTab,
  FilePreviewDirectoryTab,
  FilePreviewFileTab,
  FilePreviewLabels,
  FilePreviewTab,
  FilePreviewViewMode,
} from './types.js';

/**
 * 页签正文的三种渲染分支：内容页签整块交给宿主，目录页签多一层摘要切换，文件
 * 页签走底座。
 *
 * 单独成文件是因为三条分支的参数加起来让主组件超出了行数上限；分支本身仍是
 * 主组件的一次渲染，没有引入新的状态。
 *
 * The three ways a tab body renders: a content tab hands the whole body to the
 * host, a directory tab adds one summary-switching layer, and a file tab goes
 * through the base component.
 *
 * It lives in its own file because the three branches' parameters pushed the main
 * component past the line limit; it is still one render of that component and
 * introduces no state of its own.
 */
export interface FilePreviewPanelsProps {
  contentTab?: FilePreviewContentTab;
  directoryTab?: FilePreviewDirectoryTab;
  fileTab?: FilePreviewFileTab;
  levelKey?: string;
  onLevelChange: (key: string) => void;
  panelProps: HTMLAttributes<HTMLDivElement>;
  panelClass: string;
  text: Required<FilePreviewLabels>;
  locale?: 'en-US' | 'zh-CN';
  actions: ReturnType<typeof useFilePreviewActions>;
  actionContext: FilePreviewActions;
  editing: ReturnType<typeof useFilePreviewEditing>;
  viewMode: FilePreviewViewMode;
  setViewMode: (mode: FilePreviewViewMode) => void;
  onHandleChange: (handle: ViewerHandle | null) => void;
  onViewModesChange: (modes: readonly ViewerViewModeOption[]) => void;
  linkClickHandler?: MarkdownAdapterOptions['onLinkClick'];
  activeTab: FilePreviewTab;
}

export function FilePreviewPanels({
  levelKey,
  onLevelChange,
  contentTab,
  directoryTab,
  fileTab,
  panelProps,
  panelClass,
  text,
  locale,
  actions,
  actionContext,
  editing,
  viewMode,
  setViewMode,
  onHandleChange,
  onViewModesChange,
  linkClickHandler,
  activeTab,
}: FilePreviewPanelsProps) {
  if (contentTab) {
    // 内容页签整块交给宿主：组件只画页签行，正文不套任何容器，也没有标题行
    // ——组件不知道该在那里显示什么，套一层只会限制宿主的排版。
    //
    // A content tab hands its whole body to the host: only the tab row is ours,
    // with no container and no header in between.
    return (
      <div {...panelProps} className={`${panelClass} ${panelClass}--content`}>
        {contentTab.content}
      </div>
    );
  }

  if (directoryTab) {
    return (
      <div {...panelProps} className={panelClass}>
        <FilePreviewDirectoryBody
          tab={directoryTab}
          text={text}
          locale={locale}
          actions={actionContext}
          editing={editing}
          viewMode={viewMode}
          onViewModeChange={setViewMode}
          onHandleChange={onHandleChange}
          onViewModesChange={onViewModesChange}
          onDownload={() => {}}
          onCancelEditing={actionContext.cancelEditing}
        />
      </div>
    );
  }

  if (!fileTab) return null;

  return (
    <div {...panelProps} className={panelClass}>
      <FilePreviewHeader
        levels={fileTab.levels}
        levelKey={levelKey}
        onLevelChange={onLevelChange}
        path={fileTab.source.path}
        fileName={fileTab.source.fileName}
        canOpenParent={Boolean(fileTab.operations?.openParent)}
        titleExtra={fileTab.slots?.titleExtra}
        slotActions={fileTab.slots?.actions}
        actions={actionContext}
        text={text}
        onOpenParent={(uri) => void actions.openParent(activeTab, uri)}
      />
      <div className="ov-file-preview__body">
        <FilePreviewBody
          key={JSON.stringify([fileTab.key, levelKey])}
          fileTab={fileTab}
          text={text}
          locale={locale}
          viewMode={viewMode}
          editing={editing}
          onViewModeChange={setViewMode}
          onHandleChange={onHandleChange}
          onViewModesChange={onViewModesChange}
          onDownload={() => void actions.download(fileTab)}
          onLinkClick={linkClickHandler}
          onCancelEditing={actionContext.cancelEditing}
        />
      </div>
      {fileTab.source.truncated ? <TruncatedNotice text={text} /> : null}
      {fileTab.slots?.footer ? (
        <div className="ov-file-preview__footer">{fileTab.slots.footer}</div>
      ) : null}
    </div>
  );
}
