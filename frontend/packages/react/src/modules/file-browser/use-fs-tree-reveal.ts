import { useCallback, useEffect, useRef } from 'react';
import type { Dispatch, RefObject, SetStateAction } from 'react';
import { normalizeResourceUri } from './fs-uri.js';
import type { DirState } from './fs-model.js';
import type { RequestScopeHandle } from './use-request-scope.js';

const childPrefix = (uri: string) => (uri.endsWith('/') ? uri : `${uri}/`);

export function useFsTreeReveal({
  rootUri,
  scope,
  dirs,
  initialLoad,
  fetchPage,
  setExpanded,
}: {
  rootUri: string;
  scope: RequestScopeHandle;
  dirs: RefObject<Record<string, DirState>>;
  initialLoad: RefObject<Promise<void> | undefined>;
  fetchPage: (
    uri: string,
    offset: number,
    signal?: AbortSignal,
  ) => Promise<DirState | undefined>;
  setExpanded: Dispatch<SetStateAction<ReadonlySet<string>>>;
}) {
  const active = useRef<AbortController | null>(null);
  const cancelReveal = useCallback(() => active.current?.abort(), []);
  // Synchronize with the external network operation on unmount. Events cannot
  // observe removal of the mounted tree; cleanup aborts the active navigation.
  useEffect(() => cancelReveal, [cancelReveal]);
  const reveal = useCallback(
    async (resource: string) => {
      cancelReveal();
      const controller = new AbortController();
      active.current = controller;
      const current = scope.current();
      const valid = () =>
        !controller.signal.aborted &&
        !current.controller.signal.aborted &&
        scope.current() === current;
      const target = normalizeResourceUri(resource);
      const root = normalizeResourceUri(rootUri);
      if (target === root || !target.startsWith(childPrefix(root)))
        return false;
      await initialLoad.current;
      let parent = rootUri;
      while (valid()) {
        const cached = dirs.current[parent];
        const parentUri = normalizeResourceUri(parent);
        const prefix = childPrefix(parentUri);
        // A listing may omit virtual ancestors (e.g. viking://user) and return
        // a deeper directory directly. Follow actual rows rather than URI segments.
        const find = (page: DirState) =>
          page.entries.find((item) => {
            const uri = normalizeResourceUri(item.uri);
            return (
              uri !== parentUri &&
              uri.startsWith(prefix) &&
              (uri === target ||
                (item.isDir && target.startsWith(childPrefix(uri))))
            );
          });
        const scan = async (first: DirState | undefined) => {
          let page = first ?? (await fetchPage(parent, 0, controller.signal));
          while (valid() && page) {
            if (page.error) throw page.error;
            const entry = find(page);
            if (entry || !page.hasMore) return entry;
            const offset = page.entries.length;
            page = await fetchPage(parent, offset, controller.signal);
            if (page && page.entries.length <= offset && !page.error)
              return find(page);
          }
        };
        const usable = cached && !cached.error && !cached.loading;
        let entry = await scan(usable ? cached : undefined);
        // An import may create a new child in a directory already cached in the
        // tree. Retry from page one once before treating a cache miss as final.
        if (!entry && usable && valid()) entry = await scan(undefined);
        if (!valid()) return false;
        if (!entry || entry.access === 'denied') return false;
        if (normalizeResourceUri(entry.uri) === target) {
          if (entry.access === 'container') return false;
          return true;
        }
        if (!entry.isDir) return false;
        setExpanded((previous) => new Set(previous).add(entry.uri));
        parent = entry.uri;
      }
      return false;
    },
    [cancelReveal, dirs, fetchPage, initialLoad, rootUri, scope, setExpanded],
  );
  return { reveal, cancelReveal };
}
