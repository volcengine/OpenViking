import { useCallback, useEffect, useRef } from 'react';
import type { RefObject } from 'react';
import { useOvSignal } from '../../client/provider.js';
import {
  needsHomeIdentity,
  resourceUriIdentity,
} from '../../shared/resource-uri.js';
import type { DirState } from '../../shared/directory-tree/types.js';
import type { RequestScopeHandle } from '../../shared/request/use-request-scope.js';

const childPrefix = (uri: string) => (uri.endsWith('/') ? uri : `${uri}/`);
function isTimeoutReason(reason: unknown) {
  return (
    typeof reason === 'object' &&
    reason !== null &&
    'name' in reason &&
    reason.name === 'TimeoutError'
  );
}

export function useFsTreeReveal({
  rootUri,
  scope,
  dirs,
  initialLoad,
  isExpanded,
  fetchPage,
  expandForNavigation,
  resolveHome,
  requestSignal,
}: {
  rootUri: string;
  scope: RequestScopeHandle;
  dirs: RefObject<Record<string, DirState>>;
  initialLoad: RefObject<Promise<void> | undefined>;
  isExpanded: (uri: string) => boolean;
  fetchPage: (
    uri: string,
    offset: number,
    signal?: AbortSignal,
    refresh?: boolean,
  ) => Promise<DirState | undefined>;
  expandForNavigation: (uri: string) => void;
  resolveHome: (signal: AbortSignal) => Promise<string | undefined>;
  requestSignal?: AbortSignal;
}) {
  const inheritedSignal = useOvSignal();
  const callerSignal = requestSignal ?? inheritedSignal;
  const active = useRef<AbortController | null>(null);
  const cancelReveal = useCallback(() => active.current?.abort(), []);
  // Synchronize with the external network operation on unmount. Events cannot
  // observe removal of the mounted tree; cleanup aborts the active navigation.
  useEffect(() => cancelReveal, [cancelReveal]);
  const reveal = useCallback(
    async (resource: string, preserveExpansion = false) => {
      cancelReveal();
      const controller = new AbortController();
      active.current = controller;
      const current = scope.current();
      const signals = [controller.signal, current.controller.signal];
      if (callerSignal) signals.push(callerSignal);
      const signal = AbortSignal.any(signals);
      const valid = () => {
        if (callerSignal?.aborted && isTimeoutReason(callerSignal.reason))
          callerSignal.throwIfAborted();
        if (controller.signal.aborted || scope.current() !== current)
          return false;
        return !signal.aborted;
      };
      let home: string | undefined;
      let lookedUpHome = false;
      const identify = (uri: string) => resourceUriIdentity(uri, home);
      const loadHome = async () => {
        lookedUpHome = true;
        try {
          home = await resolveHome(signal);
        } catch (error) {
          if (valid()) throw error;
        }
      };
      if (!valid()) return false;
      if (needsHomeIdentity(rootUri, resource)) await loadHome();
      const target = identify(resource);
      const root = identify(rootUri);
      if (!valid()) return false;
      if (target !== root && !target.startsWith(childPrefix(root)))
        return false;
      await initialLoad.current;
      if (!valid()) return false;
      const expand = async (uri: string) => {
        if (!valid()) return false;
        expandForNavigation(uri);
        // The row is already located. Child refresh errors stay in its error
        // row and must not turn successful selection into a navigation failure.
        await fetchPage(uri, 0, signal, true);
        return valid();
      };
      if (target === root) return preserveExpansion || expand(rootUri);
      let parent = rootUri;
      while (valid() && (!preserveExpansion || isExpanded(parent))) {
        const cached = dirs.current[parent];
        // A listing may omit virtual ancestors (e.g. viking://user) and return
        // a deeper directory directly. Follow actual rows rather than URI segments.
        const find = async (page: DirState) => {
          const match = () => {
            const parentUri = identify(parent);
            const targetUri = identify(resource);
            const prefix = childPrefix(parentUri);
            return page.entries.find((item) => {
              const uri = identify(item.uri);
              return (
                uri !== parentUri &&
                uri.startsWith(prefix) &&
                (uri === targetUri ||
                  (item.isDir && targetUri.startsWith(childPrefix(uri))))
              );
            });
          };
          const entry = match();
          if (
            !entry &&
            !lookedUpHome &&
            page.entries.some(
              (item) =>
                needsHomeIdentity(item.uri, resource) ||
                needsHomeIdentity(item.uri, parent),
            )
          ) {
            await loadHome();
            return match();
          }
          return entry;
        };
        const scan = async (first: DirState | undefined) => {
          let page = first ?? (await fetchPage(parent, 0, signal, true));
          while (valid() && page) {
            if (page.error) throw page.error;
            const entry = await find(page);
            if (!valid()) return;
            if (entry || !page.hasMore) return entry;
            const offset = page.entries.length;
            page = await fetchPage(parent, offset, signal);
            if (page && page.entries.length <= offset && !page.error)
              return find(page);
          }
        };
        const usable =
          cached &&
          !cached.error &&
          (!cached.loading || cached.entries.length > 0);
        let entry = await scan(usable ? cached : undefined);
        // An import may create a new child in a directory already cached in the
        // tree. Retry from page one once before treating a cache miss as final.
        if (!entry && usable && valid()) entry = await scan(undefined);
        if (!valid()) return false;
        if (!entry || entry.access === 'denied') return false;
        if (identify(entry.uri) === identify(resource)) {
          if (entry.access === 'container') return false;
          return entry.isDir && !preserveExpansion ? expand(entry.uri) : true;
        }
        if (!entry.isDir) return false;
        if (!preserveExpansion) expandForNavigation(entry.uri);
        parent = entry.uri;
      }
      return false;
    },
    [
      cancelReveal,
      dirs,
      fetchPage,
      initialLoad,
      isExpanded,
      rootUri,
      scope,
      expandForNavigation,
      resolveHome,
      callerSignal,
    ],
  );
  return { reveal, cancelReveal };
}
