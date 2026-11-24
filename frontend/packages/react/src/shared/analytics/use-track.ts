import { useCallback } from 'react';
import { useAnalyticsRef } from './context.js';
import type { EventKey, Params } from './reporter.js';

type EmptyKey = {
  [K in EventKey]: Params<K> extends Readonly<Record<string, never>>
    ? K
    : never;
}[EventKey];
/** Stable, typed action collection; no payload is built when disabled. */
export function useTrack() {
  const analytics = useAnalyticsRef();
  return useCallback(
    (key: EmptyKey) => analytics.current?.emit(key, () => ({})),
    [analytics],
  );
}
