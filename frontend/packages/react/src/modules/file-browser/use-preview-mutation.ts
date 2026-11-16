import { useAnalyticsRef } from '../../shared/analytics/context.js';
import {
  beginMutation,
  type MutationTarget,
} from '../../shared/analytics/mutation.js';
import { useContext, useEffect, useMemo, useState } from 'react';
import { NotificationScopeContext } from '../../shared/feedback/notification-context.js';
import { useOvClient, useOvSignal } from '../../client/provider.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { scopeKeyOf } from './fs-scope.js';

/** A dialog owns its pending mutation. Failed submissions retain the caller's draft. */
export function usePreviewMutation(
  uri: string,
  requestOptions?: OvRequestOptions,
) {
  const analytics = useAnalyticsRef();
  const client = useOvClient();
  const feedback = useContext(NotificationScopeContext);
  const inherited = useOvSignal();
  const signal = requestOptions?.signal ?? inherited;
  const key = scopeKeyOf(client, requestOptions, uri);
  const owner = useMemo(
    () => ({ key, signal, controller: new AbortController(), pending: false }),
    [key, signal],
  );
  const [state, setState] = useState<{
    owner: typeof owner;
    pending: boolean;
    error?: unknown;
    succeeded?: boolean;
  }>();
  // Synchronize mutation transport with the committed account/resource and unmount.
  // Refresh the controller on setup so Strict Mode's cleanup/setup remains usable.
  useEffect(() => {
    if (owner.controller.signal.aborted)
      owner.controller = new AbortController();
    return () => owner.controller.abort();
  }, [owner]);
  const run = async (
    operation: (
      client: OvClient,
      options: OvRequestOptions,
    ) => Promise<unknown>,
    onSuccess?: () => void,
    analyticsTarget?: MutationTarget,
    onError?: (error: unknown) => void,
  ) => {
    if (
      !client ||
      owner.pending ||
      owner.controller.signal.aborted ||
      signal?.aborted
    )
      return;
    owner.pending = true;
    feedback?.dismiss(state?.owner === owner ? state.error : undefined);
    const composed = signal
      ? AbortSignal.any([signal, owner.controller.signal])
      : owner.controller.signal;
    setState({ owner, pending: true });
    const event = beginMutation(analytics, analyticsTarget);
    try {
      await operation(client, { ...requestOptions, signal: composed });
      if (composed.aborted) return;
      event?.success();
      setState({ owner, pending: false, succeeded: true });
      try {
        onSuccess?.();
      } catch {
        /* Notification cannot undo API success. */
      }
    } catch (error) {
      if (!composed.aborted) {
        event?.fail(error);
        setState({ owner, pending: false, ...(onError ? {} : { error }) });
        onError?.(error);
      }
    } finally {
      owner.pending = false;
    }
  };
  return {
    run,
    reset: () => {
      if (!owner.pending) {
        feedback?.dismiss(state?.owner === owner ? state.error : undefined);
        setState(undefined);
      }
    },
    pending: state?.owner === owner && state.pending,
    error: state?.owner === owner ? state.error : undefined,
    succeeded: state?.owner === owner && state.succeeded,
  };
}
