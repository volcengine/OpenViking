import type { ReactNode } from 'react';

import { FilePreviewToolbar } from './file-preview-toolbar.js';
import { FilePreviewLevelTabs } from './file-preview-level-tabs.js';
import { FilePreviewBreadcrumb } from './file-preview-breadcrumb.js';
import { FilePreviewCopyPath } from './file-preview-copy-path.js';
import { getPathFileName, splitPathSegments } from './file-preview-path.js';
import type {
  FilePreviewActions,
  FilePreviewActionSource,
  FilePreviewLabels,
  FilePreviewLevel,
  FilePreviewToolbarOptions,
} from './types.js';

/**
 * 标题行需要的最少信息。它按字段而不是按页签类型取值，文件页签与目录页签因此
 * 共用同一条渲染路径——两者的路径行本来就一样。
 *
 * The least a header needs. It reads fields rather than a tab kind, so a file tab
 * and a directory tab share one rendering path — their path rows are the same.
 */
export interface FilePreviewHeaderProps {
  /** 完整路径，显示为面包屑；省略时面包屑退化为文件名。 */
  path?: string;
  /** 无路径时回落到文件名。Falls back to the file name when there is no path. */
  fileName: string;
  /** 能否沿路径向上打开。Whether an ancestor can be opened. */
  canOpenParent: boolean;
  /** 标题旁的额外内容。Extra content next to the title. */
  titleExtra?: ReactNode;
  /** 标题行动作区，节点或接收上下文的函数。 */
  /** The header action area: a node, or a function of the actions context. */
  slotActions?: ReactNode | ((actions: FilePreviewActions) => ReactNode);
  extraActions?: ReactNode;
  actionItems?: FilePreviewActionSource;
  toolbar?: FilePreviewToolbarOptions;
  actions: FilePreviewActions;
  levels?: readonly FilePreviewLevel[];
  levelKey?: string;
  onLevelChange?: (key: string) => void;
  text: Required<FilePreviewLabels>;
  onOpenParent: (uri: string) => void;
}

export function FilePreviewHeader({
  levels,
  levelKey,
  onLevelChange,
  path,
  fileName,
  canOpenParent,
  titleExtra,
  slotActions,
  extraActions,
  actionItems,
  toolbar,
  actions,
  text,
  onOpenParent,
}: FilePreviewHeaderProps) {
  const segments = splitPathSegments(path);
  const title = getPathFileName(path) ?? fileName;
  const actionNode: ReactNode =
    typeof slotActions === 'function' ? (
      slotActions(actions)
    ) : slotActions === undefined ? (
      <FilePreviewToolbar
        key={path ?? fileName}
        options={toolbar}
        actionItems={actionItems}
        actions={actions}
        text={text}
        extraActions={extraActions}
      />
    ) : (
      slotActions
    );

  return (
    <>
      <div className="ov-file-preview__header">
        <div className="ov-file-preview__path-row">
          <FilePreviewBreadcrumb
            hiddenPathLabel={text.showHiddenPath}
            segments={segments}
            title={title}
            canOpen={canOpenParent}
            disabled={actions.busy}
            onOpen={onOpenParent}
          />
          {toolbar?.copyPath !== false && actions.canCopyPath ? (
            <FilePreviewCopyPath
              key={path}
              label={text.copyPath}
              successLabel={text.copied}
              failureLabel={text.copyFailed}
              disabled={actions.busy}
              onClick={actions.copyPath}
            />
          ) : null}
          {titleExtra}
        </div>
        {actionNode ? (
          <div className="ov-file-preview__header-actions">{actionNode}</div>
        ) : null}
      </div>
      {onLevelChange ? (
        <FilePreviewLevelTabs
          levels={levels}
          activeKey={levelKey}
          disabled={actions.busy}
          onSelect={onLevelChange}
        />
      ) : null}
    </>
  );
}
