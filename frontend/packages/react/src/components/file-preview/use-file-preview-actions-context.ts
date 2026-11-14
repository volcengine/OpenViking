import { useTrack } from '../../shared/analytics/use-track.js';
import { mediaKind } from './file-preview-media.js';
import { useCallback } from 'react';

import type { useFilePreviewActions } from './use-file-preview-actions.js';
import type { useFilePreviewEditing } from './use-file-preview-editing.js';
import type {
  FilePreviewActions,
  FilePreviewFileTab,
  FilePreviewSource,
  FilePreviewTab,
  FilePreviewViewMode,
} from './types.js';

type Operations = ReturnType<typeof useFilePreviewActions>;
type Editing = ReturnType<typeof useFilePreviewEditing>;

/**
 * 组装交给宿主的动作上下文：把组件内部的视图、编辑与操作状态，连同改变它们
 * 的方法收成一个对象，供 `slots.actions` 渲染按钮时使用。
 *
 * Assembles the action context handed to the host: the component's view, editing
 * and operation state together with the methods that change them, so a
 * `slots.actions` renderer can build its own buttons.
 */
export function useFilePreviewActionsContext({
  activeTab,
  fileTab,
  currentPath,
  currentSource,
  viewMode,
  canSwitchView,
  setViewMode,
  editing,
  operations,
}: {
  activeTab: FilePreviewTab;
  fileTab?: FilePreviewFileTab;
  /** 当前页签的路径；目录页签也有。The current tab's path; a directory has one too. */
  currentPath?: string;
  /** 当前页签的正文来源；目录页签取当前那一级。The body source; a directory uses its active level. */
  currentSource?: FilePreviewSource;
  viewMode: FilePreviewViewMode;
  canSwitchView: boolean;
  setViewMode: (mode: FilePreviewViewMode) => void;
  editing: Editing;
  operations: Operations;
}): FilePreviewActions {
  const track = useTrack();
  const selectView = (mode: FilePreviewViewMode) => {
    if (mode !== viewMode)
      track(
        mode === 'source'
          ? 'file_preview_source_view_select'
          : 'file_preview_preview_view_select',
      );
    setViewMode(mode);
  };
  const startEditing = useCallback(() => {
    if (!fileTab?.editing || mediaKind(fileTab.source) || editing.editing)
      return;
    setViewMode('source');
    void editing.start(fileTab);
  }, [editing, fileTab, setViewMode]);

  const saveEditing = useCallback(async () => {
    if (!fileTab) return false;
    const saved = await editing.save(fileTab);
    // 保存成功后回到预览视图，与进入编辑前的视图状态对齐。
    if (saved) setViewMode('preview');
    return saved;
  }, [editing, fileTab, setViewMode]);

  // 取消与保存一样回到预览视图：进入编辑时切到了源码，退出时不切回去，用户
  // 会停在源码上，而不是他进入编辑前的样子。
  //
  // Cancelling returns to the preview view just like saving does: entering editing
  // switches to source, and leaving without switching back would strand the user on
  // the source view instead of the preview they came from.
  const cancelEditing = useCallback(() => {
    editing.discard(activeTab);
    setViewMode('preview');
  }, [activeTab, editing, setViewMode]);

  // 复制路径按路径判断，不按页签类型：目录与文件都有路径行，两者都需要它。
  // Copy-path is decided by the path, not the tab kind: both a directory and a
  // file have a path row and both need it.
  const copyPath = useCallback(async () => {
    const path = currentPath;
    if (!path) return false;
    return operations.copyPath(activeTab, path);
  }, [activeTab, currentPath, operations]);

  return {
    source: currentSource,
    path: currentPath,
    viewMode,
    setViewMode: selectView,
    canSwitchView,
    editing: editing.editing,
    busy: editing.saving || editing.loadingEdit,
    canEdit:
      Boolean(fileTab?.editing) &&
      !mediaKind(fileTab!.source) &&
      !editing.editing,
    startEditing,
    saveEditing,
    cancelEditing,
    canCopyPath: Boolean(currentPath),
    copyPath,
  };
}
