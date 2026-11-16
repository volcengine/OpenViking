import { createContext, useContext, useEffect, useRef } from 'react';
import type { FilePreviewActions, FilePreviewFileTab } from './types.js';

export interface FilePreviewEditRequest {
  tabKey: string;
  onConsumed: () => void;
}

export const FilePreviewEditContext = createContext<{
  request: FilePreviewEditRequest | null;
  editFile: (() => void) | null;
} | null>(null);

export function useFilePreviewEditRequest(
  fileTab: FilePreviewFileTab | undefined,
  { busy, canEdit, editing, startEditing }: FilePreviewActions,
) {
  const request = useContext(FilePreviewEditContext)?.request;
  const consumed = useRef<FilePreviewEditRequest | null>(null);
  const ready =
    fileTab &&
    !fileTab.loading &&
    !fileTab.disabled &&
    fileTab.key === request?.tabKey;
  const editingInProgress = editing || busy;

  // Start the host's full-text load after the requested file preview commits.
  // The click runs on the dashboard, before the tab can provide its edit loader.
  // Tab switches and remounts discard pending intents and stale edit loads.
  useEffect(() => {
    if (!request || consumed.current === request || !ready) return;
    if (!editingInProgress && !canEdit) return;

    consumed.current = request;
    request.onConsumed();
    if (!editingInProgress) startEditing();
  }, [canEdit, editingInProgress, ready, request, startEditing]);
}
