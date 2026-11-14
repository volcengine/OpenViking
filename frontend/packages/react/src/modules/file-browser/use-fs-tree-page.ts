import { useMemo } from 'react';
import type { Dispatch, RefObject, SetStateAction } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { loadPage } from './fs-page.js';
import {
  emptyDir,
  isAbort,
  mergePage,
  toError,
  type DirState,
} from './fs-model.js';
import type { RequestScopeHandle } from './use-request-scope.js';

export function useFsTreePage({
  client,
  rootUri,
  requestOptions,
  scope,
  dirsRef,
  expandedRef,
  setDirs,
  setError,
}: {
  client: OvClient | null;
  rootUri: string;
  requestOptions?: OvRequestOptions;
  scope: RequestScopeHandle;
  dirsRef: RefObject<Record<string, DirState>>;
  expandedRef: RefObject<ReadonlySet<string>>;
  setDirs: Dispatch<SetStateAction<Record<string, DirState>>>;
  setError: Dispatch<SetStateAction<Error | null>>;
}) {
  return useMemo(() => {
    return async (
      uri: string,
      offset: number,
      navigationSignal?: AbortSignal,
      refresh = false,
    ) => {
      if (!client) return;
      const current = scope.current();
      const signal = navigationSignal
        ? AbortSignal.any([current.controller.signal, navigationSignal])
        : current.controller.signal;
      if (signal.aborted) return;
      const previous = dirsRef.current[uri];
      const loading = {
        ...(previous ?? emptyDir()),
        loading: true,
        error: null,
      };
      const ownsPage = () =>
        !current.controller.signal.aborted &&
        scope.current() === current &&
        dirsRef.current[uri] === loading;
      const save = (page: DirState) =>
        setDirs((dirs) => ({ ...dirs, [uri]: page }));
      const restore = () => {
        if (!ownsPage()) return;
        if (previous && !previous.loading) save(previous);
        else if (previous?.entries.length)
          save({ ...previous, loading: false });
        else {
          const next = { ...dirsRef.current };
          delete next[uri];
          setDirs(next);
        }
        if (uri === rootUri) setError(previous?.error ?? null);
      };
      if (uri === rootUri) setError(null);
      save(loading);
      signal.addEventListener('abort', restore, { once: true });
      try {
        const page = await loadPage(
          client,
          uri,
          offset,
          signal,
          requestOptions,
        );
        if (signal.aborted || !ownsPage()) return;
        let merged = mergePage(previous, page, offset);
        const pendingBranches = new Set(
          previous?.entries
            .filter((entry) => expandedRef.current.has(entry.uri))
            .map((entry) => entry.uri),
        );
        const visit = (entries: DirState['entries']) => {
          for (const entry of entries) pendingBranches.delete(entry.uri);
        };
        visit(merged.entries);
        // Refresh the loaded window atomically. A previously exhausted listing
        // stays complete, including entries shifted across a page by an import.
        // Loading or failing a later page must not discard the old visible rows.
        while (
          refresh &&
          offset === 0 &&
          previous &&
          merged.hasMore &&
          (!previous.hasMore ||
            merged.entries.length < previous.entries.length ||
            pendingBranches.size > 0)
        ) {
          const nextOffset = merged.entries.length;
          if (!nextOffset) throw new Error('Directory listing did not advance');
          const next = await loadPage(
            client,
            uri,
            nextOffset,
            signal,
            requestOptions,
          );
          if (signal.aborted || !ownsPage()) return;
          if (!next.entries.length && next.hasMore)
            throw new Error('Directory listing did not advance');
          merged = mergePage(merged, next, nextOffset);
          visit(next.entries);
        }
        save(merged);
        return merged;
      } catch (cause) {
        if (signal.aborted || !ownsPage()) return;
        if (isAbort(cause)) {
          restore();
          return;
        }
        const error = toError(cause);
        if (uri === rootUri) setError(error);
        const failed = { ...loading, loading: false, error };
        save(failed);
        return failed;
      } finally {
        signal.removeEventListener('abort', restore);
      }
    };
  }, [
    client,
    rootUri,
    requestOptions,
    scope,
    dirsRef,
    expandedRef,
    setDirs,
    setError,
  ]);
}
