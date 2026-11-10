import { useCallback, useRef, useState } from 'react';
import type {
  ViewerHandle,
  ViewerViewModeOption,
} from '@ark-design/ui/file-viewer/core';
import type { FilePreviewViewMode } from './types.js';

/** Scope modes and handles to the actual file/level, including controlled navigation. */
export function useFilePreviewViewState(identity: string) {
  const [previous, setPrevious] = useState(identity);
  const [viewMode, setViewMode] = useState<FilePreviewViewMode>('preview');
  const [viewModes, setViewModes] = useState<readonly string[]>([]);
  const viewerHandle = useRef<ViewerHandle | null>(null);
  if (previous !== identity) {
    setPrevious(identity);
    setViewMode('preview');
    setViewModes([]);
    viewerHandle.current = null;
  }
  const onHandleChange = useCallback((handle: ViewerHandle | null) => {
    viewerHandle.current = handle;
  }, []);
  const onViewModesChange = useCallback(
    (modes: readonly ViewerViewModeOption[]) => {
      const next = modes.map((mode) => mode.value);
      setViewModes((current) =>
        current.join() === next.join() ? current : next,
      );
    },
    [],
  );
  return {
    viewMode,
    setViewMode,
    viewModes,
    setViewModes,
    viewerHandle,
    onHandleChange,
    onViewModesChange,
  };
}
