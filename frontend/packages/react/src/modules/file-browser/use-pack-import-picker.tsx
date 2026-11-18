import { useCallback, useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import type { Locale } from '../../shared/locale.js';
import type { ResolvedLabels } from './file-browser-labels.js';
import { FileBrowserNativeDialog } from './file-browser-native-dialog.js';
import type { FileBrowserHandle } from './types.js';
import { supportsPack } from './preview-policy.js';

interface PackSelection {
  uri: string;
  finish: (file?: File) => void;
}

export function usePackImportPicker(
  triggerRef: RefObject<HTMLElement | null>,
  text: ResolvedLabels,
  locale: Locale,
  canWrite: boolean,
) {
  const [selection, setSelection] = useState<PackSelection>();
  const active = useRef<PackSelection | undefined>(undefined);
  const choosePackFile = useCallback<FileBrowserHandle['choosePackFile']>(
    (uri, signal) => {
      signal?.throwIfAborted();
      if (!canWrite || !supportsPack(uri))
        throw new Error(
          'OVPack import requires a supported resource directory.',
        );
      active.current?.finish();
      return new Promise<File | undefined>((resolve) => {
        const next: PackSelection = {
          uri,
          finish(file) {
            signal?.removeEventListener('abort', abort);
            if (active.current === next) {
              active.current = undefined;
              setSelection(undefined);
            }
            resolve(file);
          },
        };
        function abort() {
          next.finish();
        }
        active.current = next;
        signal?.addEventListener('abort', abort, { once: true });
        setSelection(next);
      });
    },
    [canWrite],
  );

  // Resolve the host's pending file request when the FileBrowser unmounts.
  useEffect(
    () => () => {
      const pending = active.current;
      active.current = undefined;
      pending?.finish();
    },
    [],
  );
  return {
    choosePackFile,
    dialog: selection ? (
      <FileBrowserNativeDialog
        key={selection.uri}
        kind="importPack"
        uri={selection.uri}
        canWrite={canWrite}
        canSaveSnapshot={false}
        versioned={false}
        directory
        showContent={false}
        text={text}
        locale={locale}
        triggerRef={triggerRef}
        onClose={() => selection.finish()}
        onSelectPackFile={selection.finish}
      />
    ) : null,
  };
}
