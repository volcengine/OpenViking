import { useEffect, useRef, useState } from 'react';
import { useOvClient } from '../../client/provider.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { nameFromUri } from './fs-model.js';
import { loadPage, PAGE_SIZE } from './fs-page.js';

interface DirectoryPage {
  client: OvClient;
  options?: OvRequestOptions;
  uri: string;
  children: string[];
  offset: number;
  hasMore: boolean;
  loading?: boolean;
  error?: boolean;
}

export function useSearchScopePage(
  uri: string | undefined,
  requestOptions?: OvRequestOptions,
) {
  const client = useOvClient();
  const [page, setPage] = useState<DirectoryPage>();
  const pending = useRef<AbortController | undefined>(undefined);

  // Cancel the external listing on an account or request-option change and on unmount;
  // those lifecycle changes can happen without a picker event.
  useEffect(() => () => pending.current?.abort(), [client, requestOptions]);

  function load(target: string, previous?: DirectoryPage) {
    if (!client) return;
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    const offset = previous?.offset ?? 0;
    const children = previous?.children ?? [];
    const request = {
      client,
      options: requestOptions,
      uri: target,
      children,
      offset,
      hasMore: false,
    };
    setPage({ ...request, loading: true });
    void loadPage(
      client,
      target,
      offset,
      controller.signal,
      requestOptions,
    ).then(
      (result) => {
        if (controller.signal.aborted) return;
        const directories = result.entries.flatMap((entry) => {
          if (!entry.isDir || entry.access === 'denied') return [];
          const name = nameFromUri(entry.uri);
          // Keep the selected root's alias even if the server expands it in its response.
          return name && !name.includes('/') && name !== '.' && name !== '..'
            ? [`${target}/${name}`]
            : [];
        });
        setPage({
          ...request,
          children: [...new Set([...children, ...directories])],
          offset: offset + PAGE_SIZE,
          hasMore: result.hasMore,
        });
      },
      () => {
        if (!controller.signal.aborted) setPage({ ...request, error: true });
      },
    );
  }

  function cancel() {
    pending.current?.abort();
    setPage(undefined);
  }

  return {
    page:
      page?.uri === uri &&
      page?.client === client &&
      page?.options === requestOptions
        ? page
        : undefined,
    load,
    cancel,
  };
}
