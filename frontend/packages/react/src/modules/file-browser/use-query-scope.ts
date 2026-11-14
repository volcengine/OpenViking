import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import { resolveQueryRequest } from '../../client/managed-client.js';
import { useOvQueryClient } from '../../client/query-client.js';
import { schedulerFor } from '../../client/request-scheduler.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { useOvSignal } from '../../client/provider.js';
import { toError } from './fs-model.js';

type Prepared = Awaited<ReturnType<typeof resolveQueryRequest>>;
export function useQueryScope(
  client: OvClient | null,
  options?: OvRequestOptions,
) {
  const queryClient = useOvQueryClient();
  const inheritedSignal = useOvSignal();
  const callerSignal = options?.signal ?? inheritedSignal;
  const subscribeSignal = useMemo(
    () => (notify: () => void) => {
      callerSignal?.addEventListener('abort', notify);
      return () => callerSignal?.removeEventListener('abort', notify);
    },
    [callerSignal],
  );
  const aborted = useSyncExternalStore(
    subscribeSignal,
    () => callerSignal?.aborted ?? false,
    () => false,
  );
  const scheduler = schedulerFor(queryClient);
  const input = useMemo(
    () => ({
      client,
      options: {
        baseURL: options?.baseURL,
        apiKey: options?.apiKey,
        accountId: options?.accountId,
        userId: options?.userId,
        resourceId: options?.resourceId,
        signal: options?.signal,
      },
    }),
    [
      client,
      options?.baseURL,
      options?.apiKey,
      options?.accountId,
      options?.userId,
      options?.resourceId,
      options?.signal,
    ],
  );
  const [state, setState] = useState<{
    input: typeof input;
    prepared?: Prepared;
    error?: Error;
  }>();
  const accept = useMemo(
    () => (prepared: Prepared) => {
      setState((previous) =>
        previous?.input === input &&
        JSON.stringify(previous.prepared?.scope) ===
          JSON.stringify(prepared.scope)
          ? previous
          : { input, prepared },
      );
    },
    [input],
  );
  const retryScope = useRef<() => void>(() => {});
  const retry = useCallback(() => retryScope.current(), []);
  // Resolve asynchronous identity sources against the current committed render.
  // Identity closures can change their values without changing reference. Ignore
  // superseded resolutions; equal scopes retain the existing query subscriptions.
  useEffect(() => {
    retryScope.current = () => {};
    if (!client) return;
    let active = true;
    const resolve = () => {
      void resolveQueryRequest(client, input.options, scheduler).then(
        (prepared) => {
          if (active) accept(prepared);
        },
        (error) => {
          if (active)
            setState((previous) =>
              previous?.input === input &&
              previous.error?.message === toError(error).message
                ? previous
                : { input, error: toError(error) },
            );
        },
      );
    };
    retryScope.current = resolve;
    resolve();
    return () => {
      active = false;
      retryScope.current = () => {};
    };
  });
  const prepared = state?.input === input ? state.prepared : undefined;
  const request = useMemo(
    () =>
      prepared && {
        ...prepared,
        queryClient,
        async current(signal: AbortSignal) {
          const latest = await resolveQueryRequest(
            input.client!,
            input.options,
            scheduler,
          );
          signal.throwIfAborted();
          if (JSON.stringify(latest.scope) !== JSON.stringify(prepared.scope)) {
            accept(latest);
            // Existing subscribers still own this key and its captured identity.
            // The new scope gets its own query; unsubscription handles cancellation.
            return prepared;
          }
          return latest;
        },
      },
    [prepared, input, scheduler, queryClient, accept],
  );
  return {
    request,
    aborted,
    retry,
    error: state?.input === input ? state.error : undefined,
  };
}
export type QueryRequest = NonNullable<
  ReturnType<typeof useQueryScope>['request']
>;
