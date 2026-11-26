import { useCallback, useRef, useState } from 'react';
import type { FileBrowserProps } from './types.js';

/** Tracks the effective URI and the last selection reported to a controlled host. */
export function useFileBrowserUri(
  uri: FileBrowserProps['uri'],
  defaultUri: FileBrowserProps['defaultUri'],
  onUriChange: FileBrowserProps['onUriChange'],
) {
  const [internalUri, setInternalUri] = useState<string | null>(
    () => defaultUri ?? null,
  );
  // An empty string is a controlled selection with no resource open.
  const isControlled = uri !== undefined;
  const currentUri = isControlled ? uri : internalUri;
  const reportedUri = useRef<string | undefined>(undefined);
  const changeUri = useCallback(
    (next: string | null) => {
      if (!isControlled) setInternalUri(next);
      else if (onUriChange)
        reportedUri.current = next === uri ? undefined : (next ?? undefined);
      onUriChange?.(next);
    },
    [isControlled, onUriChange, uri],
  );

  return { currentUri, isControlled, reportedUri, changeUri };
}
