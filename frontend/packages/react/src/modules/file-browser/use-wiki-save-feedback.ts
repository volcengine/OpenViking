import { useMemo, useState } from 'react';
import { useOvSignal } from '../../client/provider.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { scopeKeyOf } from '../../shared/request/fs-scope.js';
import type { WikiSaveStatus } from './fs-file-editing.js';

export interface WikiSaveFeedback {
  status: WikiSaveStatus;
}

/** A preview owns in-flight feedback, while a confirmed save survives its reload. */
export function useWikiSaveFeedback(
  client: OvClient | null,
  uri: string | null,
  requestOptions: OvRequestOptions | undefined,
) {
  const [feedback, setFeedback] = useState<
    WikiSaveFeedback & { scope: string }
  >();
  const scope = scopeKeyOf(client, requestOptions, uri ?? undefined);
  const providerSignal = useOvSignal();
  const callerSignal = requestOptions?.signal ?? providerSignal;
  const lifecycle = useMemo(
    () => ({
      reset() {
        setFeedback((current) =>
          current?.scope === scope && current.status === 'success'
            ? current
            : undefined,
        );
      },
      bind(preview: AbortSignal) {
        const signal = callerSignal
          ? AbortSignal.any([preview, callerSignal])
          : preview;
        return {
          options: { ...requestOptions, signal },
          onStatus: (status?: WikiSaveStatus) => {
            if (preview.aborted) return;
            setFeedback(
              status && !signal.aborted ? { scope, status } : undefined,
            );
          },
        };
      },
    }),
    [callerSignal, requestOptions, scope],
  );
  return [feedback?.scope === scope ? feedback : undefined, lifecycle] as const;
}
