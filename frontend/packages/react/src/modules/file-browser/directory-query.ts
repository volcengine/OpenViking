import type {
  InfiniteData,
  InfiniteQueryObserverOptions,
} from '@tanstack/query-core';
import { queryKeys } from '../../client/query-keys.js';
import { loadPage, PAGE_SIZE } from './fs-page.js';
import { isSystemUri } from './preview-policy.js';
import type { QueryRequest } from './use-query-scope.js';

export type DirectoryPage = Awaited<ReturnType<typeof loadPage>> & {
  nextOffset: number;
};
export type DirectoryOptions = InfiniteQueryObserverOptions<
  DirectoryPage,
  Error,
  InfiniteData<DirectoryPage>,
  ReturnType<typeof queryKeys.directory>,
  number
>;
export function directoryOptions(
  request: QueryRequest,
  uri: string,
): DirectoryOptions {
  const queryKey = queryKeys.directory(request.scope, {
    uri,
    output: 'agent',
    include_tags: isSystemUri(uri) ? undefined : true,
    node_limit: PAGE_SIZE,
  });
  let seen: Set<string> | undefined;
  return {
    queryKey,
    initialPageParam: 0,
    getNextPageParam: (page) => (page.hasMore ? page.nextOffset : undefined),
    queryFn: async ({ signal, pageParam }) => {
      const current = await request.current(signal);
      const previous =
        request.queryClient.getQueryData<InfiniteData<DirectoryPage, number>>(
          queryKey,
        );
      // A failed attempt must not publish its partial progress to the retry.
      const pageSeen =
        pageParam === 0
          ? new Set<string>()
          : new Set(
              seen ??
                previous?.pages.flatMap((page, index) =>
                  previous.pageParams[index]! < pageParam
                    ? page.entries.map((entry) => entry.uri)
                    : [],
                ) ??
                [],
            );
      const read = async (offset: number) => {
        const page = await loadPage(
          current.client,
          uri,
          offset,
          signal,
          current.options,
        );
        signal.throwIfAborted();
        if (
          page.hasMore &&
          (!page.entries.length ||
            page.entries.every((entry) => pageSeen.has(entry.uri)))
        )
          throw new Error('Directory listing did not advance');
        page.entries.forEach((entry) => pageSeen.add(entry.uri));
        return { ...page, nextOffset: offset + PAGE_SIZE };
      };
      let page = await read(pageParam);
      // Keep an exhausted listing complete and preserve directories shifted past
      // its old loaded window. Infinite Query commits the whole refresh atomically.
      const last = previous?.pages.at(-1);
      if (previous?.pageParams.at(-1) === pageParam && last) {
        const missing = () =>
          previous.pages.some((old) =>
            old.entries.some(
              (entry) => entry.isDir && !pageSeen.has(entry.uri),
            ),
          );
        while (page.hasMore && (!last.hasMore || missing())) {
          const next = await read(page.nextOffset);
          page = { ...next, entries: [...page.entries, ...next.entries] };
        }
      }
      seen = pageSeen;
      return page;
    },
  };
}
