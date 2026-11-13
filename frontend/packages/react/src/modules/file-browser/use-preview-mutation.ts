import { useEffect, useMemo, useState } from 'react';
import { useOvClient, useOvSignal } from '../../client/provider.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { scopeKeyOf } from './fs-scope.js';

/** A dialog owns its pending mutation. Failed submissions retain the caller's draft. */
export function usePreviewMutation(
  uri: string,
  requestOptions?: OvRequestOptions,
) {
  const client = useOvClient();
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
  ) => {
    if (
      !client ||
      owner.pending ||
      owner.controller.signal.aborted ||
      signal?.aborted
    )
      return;
    owner.pending = true;
    const composed = signal
      ? AbortSignal.any([signal, owner.controller.signal])
      : owner.controller.signal;
    setState({ owner, pending: true });
    try {
      await operation(client, { ...requestOptions, signal: composed });
      if (composed.aborted) return;
      setState({ owner, pending: false, succeeded: true });
      try {
        onSuccess?.();
      } catch {
        /* Notification cannot undo API success. */
      }
    } catch (error) {
      if (!composed.aborted) setState({ owner, pending: false, error });
    } finally {
      owner.pending = false;
    }
  };
  return {
    run,
    reset: () => {
      if (!owner.pending) setState(undefined);
    },
    pending: state?.owner === owner && state.pending,
    error: state?.owner === owner ? state.error : undefined,
    succeeded: state?.owner === owner && state.succeeded,
  };
}
