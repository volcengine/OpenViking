import { useCallback, useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import type { Locale } from '../../shared/locale.js';
import { normalizeResourceUri } from '../../shared/resource-uri.js';
import { toError } from '../../shared/request/fs-errors.js';
import type { ResolvedLabels } from './file-browser-labels.js';
import { FileBrowserNativeDialog } from './file-browser-native-dialog.js';
import type { FileBrowserHandle } from './types.js';
import { supportsPack } from './preview-policy.js';

type CheckWrite = (uri: string, signal: AbortSignal) => Promise<boolean>;

interface PackSelection {
  id: number;
  uri: string;
  checkWrite: CheckWrite;
  finish: (file?: File) => void;
  confirm: (file: File) => void;
}

export function usePackImportPicker(
  triggerRef: RefObject<HTMLElement | null>,
  text: ResolvedLabels,
  locale: Locale,
  { checkWrite }: { checkWrite: CheckWrite },
) {
  const [selection, setSelection] = useState<PackSelection>();
  const [confirmingId, setConfirmingId] = useState<number>();
  const active = useRef<PackSelection | undefined>(undefined);
  const nextId = useRef(0);
  const permissionError = text.workflow.importWriteRequired;
  const choosePackFile = useCallback<FileBrowserHandle['choosePackFile']>(
    (uri, signal) => {
      signal?.throwIfAborted();
      const target = /^viking:\/{2,}$/.test(uri)
        ? 'viking://'
        : normalizeResourceUri(uri);
      if (target !== 'viking://' && !supportsPack(target))
        throw new Error(
          'OVPack import requires a supported resource directory.',
        );
      active.current?.finish();
      return new Promise<File | undefined>((resolve, reject) => {
        const controller = new AbortController();
        let confirming = false;
        const verify = async () => {
          if (!(await checkWrite(target, controller.signal)))
            throw new Error(permissionError);
        };
        const settle = (complete: () => void) => {
          if (controller.signal.aborted) return;
          controller.abort();
          signal?.removeEventListener('abort', abort);
          if (active.current === next) {
            active.current = undefined;
            setSelection(undefined);
            setConfirmingId(undefined);
          }
          complete();
        };
        const fail = (cause: unknown) => settle(() => reject(toError(cause)));
        const next: PackSelection = {
          id: ++nextId.current,
          uri: target,
          checkWrite,
          finish(file) {
            settle(() => resolve(file));
          },
          confirm(file) {
            if (controller.signal.aborted || confirming) return;
            confirming = true;
            setConfirmingId(next.id);
            void verify().then(() => next.finish(file), fail);
          },
        };
        function abort() {
          next.finish();
        }
        active.current = next;
        signal?.addEventListener('abort', abort, { once: true });
        void verify().then(() => {
          if (active.current === next) setSelection(next);
        }, fail);
      });
    },
    [checkWrite, permissionError],
  );

  // Abort pending permission checks and release the host's file request when
  // the permission scope changes or the FileBrowser unmounts.
  useEffect(
    () => () => {
      const pending = active.current;
      if (pending?.checkWrite === checkWrite) {
        active.current = undefined;
        pending.finish();
      }
    },
    [checkWrite],
  );
  return {
    choosePackFile,
    dialog:
      selection?.checkWrite === checkWrite ? (
        <FileBrowserNativeDialog
          key={selection.id}
          kind="importPack"
          uri={selection.uri}
          canWrite
          canSaveSnapshot={false}
          versioned={false}
          directory
          showContent={false}
          text={text}
          locale={locale}
          triggerRef={triggerRef}
          onClose={() => selection.finish()}
          onSelectPackFile={selection.confirm}
          packConfirmationPending={selection.id === confirmingId}
        />
      ) : null,
  };
}
