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
  setDirs,
  setError,
}: {
  client: OvClient | null;
  rootUri: string;
  requestOptions?: OvRequestOptions;
  scope: RequestScopeHandle;
  dirsRef: RefObject<Record<string, DirState>>;
  setDirs: Dispatch<SetStateAction<Record<string, DirState>>>;
  setError: Dispatch<SetStateAction<Error | null>>;
}) {
  return useMemo(() => {
    return async (
      uri: string,
      offset: number,
      navigationSignal?: AbortSignal,
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
        const merged = mergePage(previous, page, offset);
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
  }, [client, rootUri, requestOptions, scope, dirsRef, setDirs, setError]);
}
