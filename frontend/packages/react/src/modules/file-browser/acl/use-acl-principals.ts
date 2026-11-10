import { toError } from '../fs-model.js';
import { useEffect, useState } from 'react';
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
  const key = JSON.stringify([type, keyword, open]);
  const [paging, setPaging] = useState({ key, page: 1 });
  const page = paging.key === key ? paging.page : 1;
  // Page selection belongs to one query and picker lifetime. Adjust it before
  // commit so A → B → A queries and reopening both start from page one.
  if (paging.key !== key) setPaging({ key, page: 1 });
  const [generation, setGeneration] = useState(0);
  const [state, setState] = useState({
    key: '',
    items: [] as string[],
    hasMore: false,
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error>();
  const load = options?.listPrincipals;
  // Synchronize the debounced query and page with the principal directory;
  // typing, tab changes and reopening can all change the request. Clear its timer
  // and abort on query/scope change or unmount; late responses are ignored.
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    setError(undefined);
    const timer = setTimeout(() => {
      const inherited = context.requestOptions.signal;
      const signal = inherited
        ? AbortSignal.any([controller.signal, inherited])
        : controller.signal;
      const request = {
        ...context,
        requestOptions: { ...context.requestOptions, signal },
      };
      const query = { type, keyword, page };
      void (
        load ? load(query, request) : listPrincipals(query, request, accountId)
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
  }, [open, type, keyword, key, page, generation, context, accountId, load]);

  return {
    items: state.key === key ? state.items : [],
    loading,
    error,
    hasMore: state.key === key && state.hasMore,
    more: () => {
      if (!loading) setPaging({ key, page: page + 1 });
    },
    retry: () => setGeneration((value) => value + 1),
  };
}
