import { useMemo, useState, useSyncExternalStore } from 'react';
import { useOvClient } from '../../client/provider.js';
import type { OvRequestOptions } from '../../client/types.js';
import { nameFromUri } from './fs-model.js';
import { DirectoryQueries } from './directory-queries.js';
import { useQueryScope } from './use-query-scope.js';

const noSubscribe = () => () => {};
const noSnapshot = () => undefined;

export function useSearchScopePage(
  uri: string | undefined,
  requestOptions?: OvRequestOptions,
) {
  const { request, error, aborted } = useQueryScope(
    useOvClient(),
    requestOptions,
  );
  const [target, setTarget] = useState<string>();
  const session = useMemo(
    () =>
      request && target ? new DirectoryQueries(request, target) : undefined,
    [request, target],
  );
  const dirs = useSyncExternalStore(
    aborted ? noSubscribe : (session?.subscribe ?? noSubscribe),
    session?.getSnapshot ?? noSnapshot,
    noSnapshot,
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
      if (next !== target) setTarget(next);
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
