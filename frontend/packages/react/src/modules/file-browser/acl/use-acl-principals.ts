import { toError } from '../fs-model.js';
import { useEffect, useRef, useState } from 'react';
import type { OvIdentityValue } from '../../../client/types.js';
import type {
  FileBrowserAclContext,
  FileBrowserAclOptions,
} from '../acl-types.js';
import { listPrincipals } from './acl-client.js';

export function useAclPrincipals({
  open,
  type,
  keyword,
  context,
  accountId,
  options,
}: {
  open: boolean;
  type: 'user' | 'group';
  keyword: string;
  context: FileBrowserAclContext;
  accountId?: OvIdentityValue;
  options?: FileBrowserAclOptions;
}) {
  const [page, setPage] = useState(1);
  const [generation, setGeneration] = useState(0);
  const [state, setState] = useState({
    key: '',
    items: [] as string[],
    hasMore: false,
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error>();
  const key = JSON.stringify([type, keyword]);
  const live = useRef({ context, options, accountId });
  useEffect(() => {
    live.current = { context, options, accountId };
  });

  useEffect(() => {
    setPage(1);
  }, [key, open]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    setError(undefined);
    const timer = setTimeout(() => {
      const current = live.current;
      const inherited = current.context.requestOptions.signal;
      const signal = inherited
        ? AbortSignal.any([controller.signal, inherited])
        : controller.signal;
      const request = {
        ...current.context,
        requestOptions: { ...current.context.requestOptions, signal },
      };
      const query = { type, keyword, page };
      const load = current.options?.listPrincipals;
      void (
        load
          ? load(query, request)
          : listPrincipals(query, request, current.accountId)
      ).then(
        (result) => {
          if (signal.aborted) return;
          setState((previous) => ({
            key,
            hasMore: result.hasMore,
            items: [
              ...new Set([
                ...(page > 1 && previous.key === key ? previous.items : []),
                ...result.principals,
              ]),
            ],
          }));
          setLoading(false);
        },
        (cause: unknown) => {
          if (!signal.aborted) {
            setError(toError(cause));
            setLoading(false);
          }
        },
      );
    }, 200);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [open, type, keyword, key, page, generation]);

  return {
    items: state.key === key ? state.items : [],
    loading,
    error,
    hasMore: state.key === key && state.hasMore,
    more: () => {
      if (!loading) setPage((value) => value + 1);
    },
    retry: () => setGeneration((value) => value + 1),
  };
}
