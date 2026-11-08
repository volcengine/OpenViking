import { useCallback } from 'react';

import { readCopyableText } from './file-preview-source.js';
import type { useFilePreviewActions } from './use-file-preview-actions.js';
import type { useFilePreviewEditing } from './use-file-preview-editing.js';
import type {
  FilePreviewActions,
  FilePreviewFileTab,
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
  viewMode,
  canSwitchView,
  setViewMode,
  editing,
  operations,
}: {
  activeTab: FilePreviewTab;
  fileTab?: FilePreviewFileTab;
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

  const cancelEditing = useCallback(
    () => editing.discard(activeTab),
    [activeTab, editing],
  );

  const copyContent = useCallback(async () => {
    const copyable = fileTab && readCopyableText(fileTab.source);
    if (!fileTab || !copyable) return false;
    return operations.copyContent(fileTab, copyable);
  }, [fileTab, operations]);

  const copyPath = useCallback(async () => {
    const path = fileTab?.source.path;
    if (!path) return false;
    return operations.copyPath(activeTab, path);
  }, [activeTab, fileTab, operations]);

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
    canCopyContent: Boolean(fileTab && readCopyableText(fileTab.source)),
    copyContent,
    canCopyPath: Boolean(fileTab?.source.path),
    copyPath,
    canDownload: Boolean(fileTab?.operations?.download),
    download,
  };
}
