import { useImperativeHandle, type Ref } from 'react';
import { useTrack } from '../../shared/analytics/use-track.js';
import type { FilePreviewHandle, FilePreviewTab } from './types.js';
import type { useFilePreviewEditing } from './use-file-preview-editing.js';
import type { useFilePreviewViewState } from './use-file-preview-view-state.js';
import { afterLeaving } from './use-file-preview-leave.js';

interface Options extends Pick<
  ReturnType<typeof useFilePreviewViewState>,
  'setViewMode' | 'setViewModes' | 'viewerHandle'
> {
  tabs: readonly FilePreviewTab[];
  activeTab: FilePreviewTab;
  currentKey: string;
  levelKey?: string;
  selectLevel: (key: string) => void;
  editing: ReturnType<typeof useFilePreviewEditing>;
  onSelect?: (key: string) => void;
  onActiveKeyChange?: (key: string) => void;
  confirmLeave: FilePreviewHandle['confirmLeave'];
}

export function useFilePreviewNavigation(
  ref: Ref<FilePreviewHandle>,
  {
    tabs,
    activeTab,
    currentKey,
    levelKey,
    selectLevel,
    editing,
    onSelect,
    onActiveKeyChange,
    setViewMode,
    setViewModes,
    viewerHandle,
    confirmLeave,
  }: Options,
) {
  const track = useTrack();
  const select = (key: string) => {
    if (editing.saving || (key === currentKey && key === activeTab.key))
      return false;
    const target = tabs.find((tab) => tab.key === key && !tab.disabled);
    if (!target) return false;
    // Sync an invalid controlled key to the rendered tab without leaving its draft.
    const switching = key !== activeTab.key;
    return afterLeaving(!switching || confirmLeave(), () => {
      if (switching) {
        editing.discard(activeTab);
        setViewMode('preview');
        setViewModes([]);
        viewerHandle.current = null;
      }
      track('file_preview_tab_change');
      onSelect?.(key);
      onActiveKeyChange?.(key);
    });
  };
  useImperativeHandle(ref, () => ({
    setActiveTab: select,
    confirmLeave,
    getActiveTab: () => activeTab.key,
    getViewerHandle: () =>
      activeTab.kind === 'file' ? viewerHandle.current : null,
  }));
  return {
    selectTab: select,
    selectLevel: (key: string) => {
      if (editing.saving || editing.loadingEdit || key === levelKey) return;
      afterLeaving(confirmLeave(), () => {
        // Controlled selections may be declined; their draft stays with the current level.
        if (activeTab.kind !== 'file' || activeTab.activeLevelKey === undefined)
          editing.discard(activeTab);
        selectLevel(key);
      });
    },
  };
}
