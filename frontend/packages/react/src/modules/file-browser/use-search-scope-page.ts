import { useMemo, useState, useSyncExternalStore } from 'react';
import { useOvClient } from '../../client/provider.js';
import type { OvRequestOptions } from '../../client/types.js';
import { nameFromUri } from '../../shared/directory-tree/rows.js';
import { DirectoryQueries } from '../../shared/directory-tree/directory-queries.js';
import { useQueryScope } from '../../shared/request/use-query-scope.js';
import { useUserRootMode } from '../../shared/directory-tree/user-root-context.js';

const noSubscribe = () => () => {};
const noSnapshot = () => undefined;

export function useSearchScopePage(
  uri: string | undefined,
  requestOptions?: OvRequestOptions,
) {
  const userRootMode = useUserRootMode();
  const { request, error, aborted, retry } = useQueryScope(
    useOvClient('local'),
    requestOptions,
    'file_browser_directory_load',
  );
  const [target, setTarget] = useState<string>();
  const session = useMemo(
    () =>
      request && target
        ? new DirectoryQueries(request, target, undefined, userRootMode)
        : undefined,
    [request, target, userRootMode],
  );
  const dirs = useSyncExternalStore(
    aborted ? noSubscribe : (session?.subscribe ?? noSubscribe),
    session?.getSnapshot ?? noSnapshot,
  );
  const current = target ? dirs?.[target] : undefined;
  const page =
    target && uri === target && (current || error)
      ? {
          uri: target,
          children: [
            ...new Set(
              current?.entries.flatMap((entry) => {
                if (!entry.isDir || entry.access === 'denied') return [];
                const name = nameFromUri(entry.uri);
                return name && name !== '.' && name !== '..'
                  ? [`${target.replace(/\/$/, '')}/${name}`]
                  : [];
              }) ?? [],
            ),
          ],
          hasMore: current?.hasMore ?? false,
          loading: current?.loading,
          error: !!(error ?? current?.error),
        }
      : undefined;
  return {
    page,
    load: (next: string, previous?: { error?: boolean }) => {
      if (aborted) return;
      if (next !== target) setTarget(next);
      else if (!request && error) retry();
      else if (session)
        void session.load(
          next,
          previous?.error ? 0 : 1,
          undefined,
          previous?.error,
        );
    },
    cancel: () => setTarget(undefined),
  };
}
