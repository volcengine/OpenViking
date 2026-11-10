import type { ReactNode } from 'react';

import { FilePreviewBreadcrumb } from './file-preview-breadcrumb.js';
import { FilePreviewCopyPath } from './file-preview-copy-path.js';
import { getPathFileName, splitPathSegments } from './file-preview-path.js';
import type { FilePreviewActions } from './types.js';

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
  actions: FilePreviewActions;
  text: { copyPath: string; showHiddenPath: string };
  onOpenParent: (uri: string) => void;
}

/**
 * 内容区标题行：路径面包屑、复制路径与宿主提供的动作区。
 *
 * 复制路径是外壳的一部分——有 `source.path` 的文件在任何宿主下都需要它，因此
 * 内置在面包屑末尾。其余动作（复制正文、下载、视图切换、编辑）依赖宿主的产品
 * 语义与文案，由宿主在 `slots.actions` 里用 `actions` 上下文自行渲染。
 *
 * The content header: the path breadcrumb, copy-path, and a host-supplied action
 * area.
 *
 * Copy-path belongs to the shell — any file with a `source.path` needs it under
 * every host — so it sits at the end of the breadcrumb. The other actions (copy
 * body, download, view switch, edit) carry host product semantics and copy, and
 * the host renders them in `slots.actions` from the `actions` context.
 */
export function FilePreviewHeader({
  path,
  fileName,
  canOpenParent,
  titleExtra,
  slotActions,
  actions,
  text,
  onOpenParent,
}: FilePreviewHeaderProps) {
  const segments = splitPathSegments(path);
  const title = getPathFileName(path) ?? fileName;
  const actionNode: ReactNode =
    typeof slotActions === 'function' ? slotActions(actions) : slotActions;

  return (
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
        {actions.canCopyPath ? (
          <FilePreviewCopyPath
            label={text.copyPath}
            disabled={actions.busy}
            onClick={() => void actions.copyPath()}
          />
        ) : null}
        {titleExtra}
      </div>
      {actionNode ? (
        <div className="ov-file-preview__header-actions">{actionNode}</div>
      ) : null}
    </div>
  );
}
