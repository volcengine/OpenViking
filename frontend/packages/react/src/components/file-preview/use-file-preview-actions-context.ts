import { useCallback } from 'react';

import { readCopyableText } from './file-preview-source.js';
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
  const startEditing = useCallback(() => {
    if (!fileTab) return;
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

  // 复制正文同样按来源判断：目录的当前摘要也是一段可复制的文本。
  // Copy-content is decided by the source too: a directory's active summary is
  // copyable text as well.
  const copyable = currentSource ? readCopyableText(currentSource) : undefined;
  const copyContent = useCallback(async () => {
    if (!copyable) return false;
    return operations.copyContent(activeTab, copyable);
  }, [activeTab, copyable, operations]);

  // 复制路径按路径判断，不按页签类型：目录与文件都有路径行，两者都需要它。
  // Copy-path is decided by the path, not the tab kind: both a directory and a
  // file have a path row and both need it.
  const copyPath = useCallback(async () => {
    const path = currentPath;
    if (!path) return false;
    return operations.copyPath(activeTab, path);
  }, [activeTab, currentPath, operations]);

  const download = useCallback(() => {
    if (fileTab) void operations.download(fileTab);
  }, [fileTab, operations]);

  return {
    viewMode,
    setViewMode,
    canSwitchView,
    editing: editing.editing,
    busy: editing.saving || editing.loadingEdit,
    canEdit: Boolean(fileTab?.editing) && !editing.editing,
    startEditing,
    saveEditing,
    cancelEditing,
    canCopyContent: Boolean(copyable),
    copyContent,
    canCopyPath: Boolean(currentPath),
    copyPath,
    canDownload: Boolean(fileTab?.operations?.download),
    download,
  };
}
