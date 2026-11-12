import { useCallback } from 'react';
import type { FilePreviewProps, FilePreviewTab } from './types.js';
import type { useFilePreviewEditing } from './use-file-preview-editing.js';
import type { useFilePreviewViewState } from './use-file-preview-view-state.js';

export function useFilePreviewSwitchTab({
  activeKey,
  activeTab,
  currentKey,
  editing,
  onActiveKeyChange,
  tabs,
  setInternalKey,
  setViewMode,
  setViewModes,
  viewerHandle,
}: Pick<FilePreviewProps, 'activeKey' | 'onActiveKeyChange' | 'tabs'> &
  Pick<
    ReturnType<typeof useFilePreviewViewState>,
    'setViewMode' | 'setViewModes' | 'viewerHandle'
  > & {
    activeTab: FilePreviewTab;
    currentKey: string;
    editing: ReturnType<typeof useFilePreviewEditing>;
    setInternalKey: (key: string) => void;
  }) {
  return useCallback(
    (key: string) => {
      if (editing.saving || editing.noteOpen) return false;
      // 只有「请求的 key 就是目标」才算已在该页签。当前页签被移除时 `currentKey`
      // 仍指向它、`activeTab` 已回落到 `tabs[0]`，此时点 `tabs[0]` 必须放行让宿主
      // 同步 `activeKey`，否则正文显示 B 而页签栏无选中项，无法修正。
      // Only "requested key is the target" counts as already there; a stale
      // `currentKey` must not block selecting the tab that actually renders.
      if (key === currentKey && key === activeTab.key) return false;
      const target = tabs.find((tab) => tab.key === key);
      if (!target || target.disabled) return false;
      // 请求的 key 过期、但目标就是当前实际渲染的页签时，只是把宿主的
      // `activeKey` 同步到有效值，并没有离开这个页签——不能丢弃它的编辑态，
      // 否则用户在 B 上编辑、点一下已选中的 B 就会丢掉草稿。
      //
      // When the requested key is stale but the target is the tab that already
      // renders, this only syncs the host's `activeKey`; the user never left the
      // tab, so its editing state must survive — otherwise editing B and clicking
      // the already-selected B would drop the draft.
      const syncingOnly = target.key === activeTab.key;
      if (!syncingOnly) {
        editing.discard(activeTab);
        setViewMode('preview');
        // 视图模式属于上一个页签的格式，切换后等新格式上报再决定是否显示切换。
        setViewModes([]);
        viewerHandle.current = null;
      }
      if (activeKey === undefined) setInternalKey(key);
      onActiveKeyChange?.(key);
      return true;
    },
    [
      activeKey,
      activeTab,
      currentKey,
      editing,
      onActiveKeyChange,
      tabs,
      setInternalKey,
      setViewMode,
      setViewModes,
      viewerHandle,
    ],
  );
}
