import { useEffect, useRef } from 'react';
import type { FileBrowserAclLevel } from '../acl-types.js';

/** Revalidate the active resource before opening its permission manager. */
export function useAclManager({
  key,
  revision,
  uri,
  loadPermission,
  onLevel,
  onError,
}: {
  key: string;
  revision: number;
  uri: string | null;
  loadPermission: (
    uri: string,
    signal: AbortSignal,
  ) => Promise<FileBrowserAclLevel | undefined>;
  onLevel: (level: FileBrowserAclLevel | undefined) => void;
  onError: () => void;
}) {
  const pending = useRef<AbortController | null>(null);
  // The probe outlives its click. Abort on a new server-permission revision,
  // resource/request scope change or unmount so stale access cannot reopen it.
  useEffect(
    () => () => {
      pending.current?.abort();
      pending.current = null;
    },
    [key, revision, loadPermission],
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
