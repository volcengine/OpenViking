import type { ReactNode } from 'react';

import { FilePreviewLevelTabs } from './file-preview-level-tabs.js';
import { FilePreviewBreadcrumb } from './file-preview-breadcrumb.js';
import { FilePreviewCopyPath } from './file-preview-copy-path.js';
import { getPathFileName, splitPathSegments } from './file-preview-path.js';
import type { FilePreviewActions, FilePreviewFileTab } from './types.js';

export interface FilePreviewHeaderProps {
  fileTab: FilePreviewFileTab;
  actions: FilePreviewActions;
  levelKey?: string;
  onLevelChange: (key: string) => void;
  text: { copyPath: string };
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
  fileTab,
  levelKey,
  onLevelChange,
  actions,
  text,
  onOpenParent,
}: FilePreviewHeaderProps) {
  const path = fileTab.source.path;
  const segments = splitPathSegments(path);
  const title = getPathFileName(path) ?? fileTab.source.fileName;

  const slotActions = fileTab.slots?.actions;
  const actionNode: ReactNode =
    typeof slotActions === 'function' ? slotActions(actions) : slotActions;

  return (
    <>
      <div className="ov-file-preview__header">
        <div className="ov-file-preview__path-row">
          <FilePreviewBreadcrumb
            segments={segments}
            title={title}
            canOpen={Boolean(fileTab.operations?.openParent)}
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
          {fileTab.slots?.titleExtra}
        </div>
        {actionNode ? (
          <div className="ov-file-preview__header-actions">{actionNode}</div>
        ) : null}
      </div>
      <FilePreviewLevelTabs
        levels={fileTab.levels}
        activeKey={levelKey}
        disabled={actions.busy}
        onSelect={onLevelChange}
      />
    </>
  );
}
