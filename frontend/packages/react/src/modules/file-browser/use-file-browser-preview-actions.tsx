import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import { useOvClient, useOvSignal } from '../../client/provider.js';
import { OV_PATHS } from '../../client/paths.js';
import type { OvRequestOptions } from '../../client/types.js';
import type { FilePreviewMenuItem } from '../../components/file-preview/index.js';
import type { Locale } from '../../shared/locale.js';
import HistoryIcon from '../../../../icons/svg/file-preview/history.svg?react';
import ImportIcon from '../../../../icons/svg/file-preview/import.svg?react';
import ExportIcon from '../../../../icons/svg/file-preview/export.svg?react';
import DeleteIcon from '../../../../icons/svg/file-preview/delete.svg?react';
import { FileBrowserDeleteDialog } from './file-browser-delete-dialog.js';
import { scopeKeyOf } from './fs-scope.js';
import type { ResolvedLabels } from './file-browser-labels.js';
import type { FileBrowserPreviewActions } from './preview-types.js';

export function useFileBrowserPreviewActions({
  uri,
  rootUri,
  isFile,
  canWrite,
  canManage,
  options,
  locale,
  text,
  requestOptions,
  triggerRef,
  onDeleted,
}: {
  uri: string | null;
  rootUri: string;
  isFile: boolean;
  canWrite: boolean;
  canManage: boolean;
  options?: FileBrowserPreviewActions;
  locale: Locale;
  text: ResolvedLabels;
  requestOptions?: OvRequestOptions;
  triggerRef: RefObject<HTMLElement | null>;
  onDeleted: (uri: string) => void;
}) {
  const client = useOvClient();
  const inheritedSignal = useOvSignal();
  const signal = requestOptions?.signal ?? inheritedSignal;
  const key = `${scopeKeyOf(client, requestOptions, rootUri)}:${uri}`;
  const scope = useRef(key);
  scope.current = key;
  const controller = useRef<AbortController | undefined>(undefined);
  const [state, setState] = useState<{
    key: string;
    open: boolean;
    busy?: boolean;
    error?: unknown;
  }>();
  // Abort requests and discard confirmations when the resource or account changes.
  useEffect(
    () => () => {
      controller.current?.abort();
      controller.current = undefined;
    },
    [key, signal],
  );
  const canDelete = Boolean(
    client &&
    uri &&
    uri !== rootUri &&
    isFile &&
    canManage &&
    options?.delete !== false,
  );
  const deleteFile = () => {
    if (canDelete && !controller.current) setState({ key, open: true });
  };
  const confirm = async () => {
    if (!canDelete || !client || !uri || controller.current) return;
    const pending = new AbortController();
    controller.current = pending;
    const composed = signal
      ? AbortSignal.any([signal, pending.signal])
      : pending.signal;
    setState({ key, open: true, busy: true });
    try {
      await client.request({
        method: 'DELETE',
        path: OV_PATHS.fsRemove,
        query: { uri, wait: true },
        options: { ...requestOptions, signal: composed },
      });
      if (composed.aborted || scope.current !== key) return;
      setState({ key, open: false });
      try {
        onDeleted(uri);
      } catch {
        /* Navigation cannot undo deletion. */
      }
      // Notifications do not change the result of a successful server operation.
      try {
        options?.onDelete?.(uri);
      } catch {
        /* Host notification only. */
      }
    } catch (error) {
      if (!composed.aborted && scope.current === key)
        setState({ key, open: true, error });
    } finally {
      if (controller.current === pending) {
        controller.current = undefined;
        setState((current) =>
          current?.key === key ? { ...current, busy: false } : current,
        );
      }
    }
  };
  const items: FilePreviewMenuItem[] = [];
  if (uri) {
    if (options?.onHistory)
      items.push({
        key: 'history',
        label: text.history,
        icon: <HistoryIcon width={16} height={16} />,
        onSelect: () => options.onHistory!(uri),
      });
    if (options?.onImportPack && canWrite)
      items.push({
        key: 'importPack',
        label: text.importPack,
        icon: <ImportIcon width={16} height={16} />,
        separatorBefore: true,
        onSelect: () =>
          options.onImportPack!(
            isFile ? uri.slice(0, uri.lastIndexOf('/')) : uri,
          ),
      });
    if (options?.onExportPack)
      items.push({
        key: 'exportPack',
        label: text.exportPack,
        icon: <ExportIcon width={16} height={16} />,
        separatorBefore: !options.onImportPack || !canWrite,
        onSelect: () => options.onExportPack!(uri),
      });
    if (canDelete)
      items.push({
        key: 'delete',
        label: text.deleteFile,
        icon: <DeleteIcon width={16} height={16} />,
        danger: true,
        separatorBefore: true,
        onSelect: deleteFile,
      });
  }
  return {
    menuItems: items,
    context: { canDelete, deleteFile },
    dialog: uri ? (
      <FileBrowserDeleteDialog
        key={key}
        open={state?.key === key && state.open && canDelete}
        uri={uri}
        busy={state?.key === key && Boolean(state.busy)}
        error={state?.key === key ? state.error : undefined}
        text={text}
        locale={locale}
        triggerRef={triggerRef}
        onClose={() =>
          setState((current) =>
            current ? { ...current, open: false } : current,
          )
        }
        onConfirm={() => void confirm()}
      />
    ) : null,
  };
}
