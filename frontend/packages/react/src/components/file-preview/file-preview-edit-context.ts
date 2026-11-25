import { createContext, useCallback, useContext, useRef } from 'react';
import type { FilePreviewActions, FilePreviewFileTab } from './types.js';

type StartEditingAtAnnotation = (matchText?: string) => void;
export type FilePreviewPanelActions = FilePreviewActions & {
  startEditingAtAnnotation: StartEditingAtAnnotation;
};

export const FilePreviewEditContext = createContext<{
  editFile: ((matchText: string) => void) | null;
  onFileReady: (key: string, startEditing?: StartEditingAtAnnotation) => void;
} | null>(null);

export function useFilePreviewReadyRef(
  fileTab: FilePreviewFileTab | undefined,
  { busy, canEdit, startEditingAtAnnotation }: FilePreviewPanelActions,
  editEnabled: boolean,
) {
  const panelRef = useRef<HTMLDivElement>(null);
  const onFileReady = useContext(FilePreviewEditContext)?.onFileReady;
  const readyKey = fileTab?.loading ? undefined : fileTab?.key;
  const edit =
    onFileReady && editEnabled && canEdit && !busy && !fileTab?.disabled
      ? startEditingAtAnnotation
      : undefined;
  // Continue the click when the target file panel attaches with its loaded tab.
  const attachPanel = useCallback(
    (node: HTMLDivElement | null) => {
      panelRef.current = node;
      if (node && readyKey !== undefined) onFileReady?.(readyKey, edit);
    },
    [edit, onFileReady, readyKey],
  );
  return { panelRef, attachPanel };
}
