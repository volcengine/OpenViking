import { beginWorkflow } from '../../shared/monitoring/workflow.js';
import { now } from '../../shared/monitoring/clock.js';
import { bindClient, bindingOf } from '../../shared/monitoring/client.js';
import {
  rawClient,
  type BoundClient,
} from '../../shared/monitoring/binding.js';
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
import { toError } from './fs-errors.js';

async function resolveMonitoredRequest(
  ...[client, options, scheduler]: Parameters<typeof resolveQueryRequest>
) {
  const binding = bindingOf(client);
  const prepared = await resolveQueryRequest(
    (client as OvClient & BoundClient)[rawClient] ?? client,
    options,
    scheduler,
  );
  return {
    ...prepared,
    client: binding ? bindClient(prepared.client, binding) : prepared.client,
  };
}
type Prepared = Awaited<ReturnType<typeof resolveMonitoredRequest>>;
export function useQueryScope(
  client: OvClient | null,
  options?: OvRequestOptions,
  target?: Parameters<typeof beginWorkflow>[1],
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
    const resolve = (retry = false) => {
      const binding = bindingOf(client);
      const epoch = binding?.controller.epoch;
      const started = binding?.controller.options ? now() : undefined;
      void resolveMonitoredRequest(client, input.options, scheduler).then(
        (prepared) => {
          if (active) accept(prepared);
        },
        (error) => {
          const cause = toError(error);
          if (active)
            setState((previous) =>
              previous?.input === input &&
              previous.error?.message === toError(error).message
                ? previous
                : { input, error: toError(error) },
            );
          if (
            active &&
            target &&
            started !== undefined &&
            binding?.controller.epoch === epoch &&
            (retry ||
              state?.input !== input ||
              state.error?.message !== cause.message)
          ) {
            const operation = beginWorkflow(client, target, callerSignal, {
              retryError: retry ? state?.error : undefined,
            });
            operation?.span.timing(started, now(), Date.now());
            operation?.fail(cause, 'configuration', callerSignal);
          }
        },
      );
    };
    retryScope.current = () => resolve(true);
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
          const latest = await resolveMonitoredRequest(
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
