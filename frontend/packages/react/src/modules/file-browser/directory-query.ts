import { traceLink } from '../../shared/monitoring/trace/link.js';
import { tagModel, tagData, readData } from '../../shared/monitoring/data.js';
import { bindingOf } from '../../shared/monitoring/client.js';
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
  retryError?: () => unknown,
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
      let retryCause = retryError?.();
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
          retryCause,
        );
        retryCause = undefined;
        signal.throwIfAborted();
        if (
          page.hasMore &&
          (!page.entries.length ||
            page.entries.every((entry) => pageSeen.has(entry.uri)))
        ) {
          const error = new Error('Directory listing did not advance');
          page.monitoring?.fail(error, 'protocol', signal);
          throw error;
        }
        page.entries.forEach((entry) => pageSeen.add(entry.uri));
        const { monitoring, ...data } = page;
        const result = { ...data, nextOffset: offset + PAGE_SIZE };
        tagModel(bindingOf(current.client), result, monitoring?.span);
        return result;
      };
      let page = await read(pageParam);
      const inputs = readData(bindingOf(current.client), [page]);
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
          inputs.push(...readData(bindingOf(current.client), [next]));
          page = { ...next, entries: [...page.entries, ...next.entries] };
        }
      }
      const binding = bindingOf(current.client);
      const query = request.queryClient
        .getQueryCache()
        .find({ queryKey, exact: true });
      if (pageParam > 0)
        for (const span of inputs)
          for (const previous of readData(binding, [query])) {
            if (span !== previous)
              span.history.addLink(
                traceLink(
                  previous.snapshot(),
                  previous.trace.spanId,
                  'render-input',
                ),
              );
          }
      tagData(binding, query, inputs);
      seen = pageSeen;
      return page;
    },
  };
}
