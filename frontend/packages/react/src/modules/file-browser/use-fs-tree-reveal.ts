import { useCallback, useEffect, useRef } from 'react';
import type { Dispatch, RefObject, SetStateAction } from 'react';
import { useOvSignal } from '../../client/provider.js';
import { isHomeResourceUri, resourceUriIdentity } from './fs-uri.js';
import type { DirState } from './fs-model.js';
import type { RequestScopeHandle } from './use-request-scope.js';

const childPrefix = (uri: string) => (uri.endsWith('/') ? uri : `${uri}/`);
const isUserUri = (uri: string) =>
  uri === 'viking://user' || uri.startsWith('viking://user/');

const needsHomeIdentity = (left: string, right: string) =>
  (isHomeResourceUri(left) && isUserUri(right)) ||
  (isHomeResourceUri(right) && isUserUri(left));

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
  fetchPage,
  setExpanded,
  resolveHome,
  requestSignal,
}: {
  rootUri: string;
  scope: RequestScopeHandle;
  dirs: RefObject<Record<string, DirState>>;
  initialLoad: RefObject<Promise<void> | undefined>;
  fetchPage: (
    uri: string,
    offset: number,
    signal?: AbortSignal,
    refresh?: boolean,
  ) => Promise<DirState | undefined>;
  setExpanded: Dispatch<SetStateAction<ReadonlySet<string>>>;
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
    async (resource: string) => {
      cancelReveal();
      const controller = new AbortController();
      active.current = controller;
      const current = scope.current();
      const signals = [controller.signal, current.controller.signal];
      if (callerSignal) signals.push(callerSignal);
      const signal = AbortSignal.any(signals);
      const valid = () => {
        if (controller.signal.aborted || scope.current() !== current)
          return false;
        if (callerSignal?.aborted && isTimeoutReason(callerSignal.reason))
          callerSignal.throwIfAborted();
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
      const expand = async (uri: string) => {
        if (!valid()) return false;
        setExpanded((previous) => new Set(previous).add(uri));
        // The row is already located. Child refresh errors stay in its error
        // row and must not turn successful selection into a navigation failure.
        await fetchPage(uri, 0, signal, true);
        return valid();
      };
      if (target === root) return expand(rootUri);
      let parent = rootUri;
      while (valid()) {
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
          let page = first ?? (await fetchPage(parent, 0, signal));
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
        const usable = cached && !cached.error && !cached.loading;
        let entry = await scan(usable ? cached : undefined);
        // An import may create a new child in a directory already cached in the
        // tree. Retry from page one once before treating a cache miss as final.
        if (!entry && usable && valid()) entry = await scan(undefined);
        if (!valid()) return false;
        if (!entry || entry.access === 'denied') return false;
        if (identify(entry.uri) === identify(resource)) {
          if (entry.access === 'container') return false;
          return entry.isDir ? expand(entry.uri) : true;
        }
        if (!entry.isDir) return false;
        setExpanded((previous) => new Set(previous).add(entry.uri));
        parent = entry.uri;
      }
      return false;
    },
    [
      cancelReveal,
      dirs,
      fetchPage,
      initialLoad,
      rootUri,
      scope,
      setExpanded,
      resolveHome,
      callerSignal,
    ],
  );
  return { reveal, cancelReveal };
}
