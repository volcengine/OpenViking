import { useEffect, useRef } from 'react';
import type { FileBrowserAclLevel } from '../acl-types.js';

/** Revalidate the active resource before opening its permission manager. */
export function useAclManager({
  key,
  uri,
  loadPermission,
  onLevel,
  onError,
}: {
  key: string;
  uri: string | null;
  loadPermission: (
    uri: string,
    signal: AbortSignal,
  ) => Promise<FileBrowserAclLevel | undefined>;
  onLevel: (level: FileBrowserAclLevel | undefined) => void;
  onError: () => void;
}) {
  const pending = useRef<AbortController | null>(null);
  // The probe outlives its click. Abort on resource/request scope changes or
  // unmount so an old account cannot open a manager in the new scope.
  useEffect(
    () => () => {
      pending.current?.abort();
      pending.current = null;
    },
    [key, loadPermission],
  );
  return async () => {
    if (!uri || pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    try {
      const level = await loadPermission(uri, controller.signal);
      if (!controller.signal.aborted) onLevel(level);
    } catch {
      if (!controller.signal.aborted) onError();
    } finally {
      if (pending.current === controller) pending.current = null;
    }
  };
}
