import { useEffect, useMemo, useState } from 'react';
import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { mergeFindResult, type FindResult } from './find-result.js';
import { isAbort, toError, type FsEntry } from './fs-model.js';

export const FILE_SEARCH_LIMIT = 20;
const SEARCH_DELAY = 300;

interface SearchRequest {
  client: OvClient;
  query: string;
  targetUri: string;
  options?: OvRequestOptions;
}

interface SearchResponse {
  request: SearchRequest;
  results: FsEntry[];
  error?: Error;
}

export function useFsSearch({
  client,
  query,
  targetUri,
  generation,
  requestOptions,
}: {
  client: OvClient | null;
  query: string;
  targetUri: string;
  generation: number;
  requestOptions?: OvRequestOptions;
}) {
  const request = useMemo(
    () =>
      client && query
        ? { client, query, targetUri, options: requestOptions, generation }
        : null,
    [client, query, targetUri, requestOptions, generation],
  );
  const [response, setResponse] = useState<SearchResponse | null>(null);

  // The committed input schedules a network request. Debouncing keeps typing
  // responsive; cleanup clears the timer and aborts any request for the old
  // query, identity or closed dialog. Late custom-transport results are ignored.
  useEffect(() => {
    if (!request) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void request.client
        .request<FindResult>({
          method: 'POST',
          path: OV_PATHS.searchFind,
          body: {
            query: request.query,
            target_uri: request.targetUri,
            limit: FILE_SEARCH_LIMIT,
          },
          options: { ...request.options, signal: controller.signal },
        })
        .then(
          (result) => {
            if (!controller.signal.aborted)
              setResponse({
                request,
                results: mergeFindResult(result).slice(0, FILE_SEARCH_LIMIT),
              });
          },
          (cause: unknown) => {
            if (!controller.signal.aborted && !isAbort(cause))
              setResponse({ request, results: [], error: toError(cause) });
          },
        );
    }, SEARCH_DELAY);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [request]);

  const current = request && response?.request === request ? response : null;
  return {
    results: current?.results ?? null,
    loading: !!request && !current,
    error: current?.error,
  };
}
