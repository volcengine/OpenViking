import { type RefObject, useCallback, useEffect, useRef } from 'react';
import { useAnalytics } from './context.js';
import { observeExposure } from './observe-exposure.js';
import type { Params, ShowKey } from './reporter.js';

/** Callback refs install observers only on committed DOM, with React 18/19 cleanup. */
export function useExposure<K extends ShowKey>(
  key: K,
  identity: unknown = key,
  params?: () => Params<K>,
  active = true,
) {
  const analytics = useAnalytics();
  const previous = useRef<{ identity: unknown; shown: boolean }>({
    identity,
    shown: false,
  });
  // A committed close begins a new exposure cycle even when a parent keeps the
  // collector mounted. No external resource is installed by this reset.
  useEffect(() => {
    if (!active || !Object.is(previous.current.identity, identity))
      previous.current = { identity, shown: false };
  }, [active, identity]);
  const cleanup = useRef<(() => void) | undefined>(undefined);
  const ref = useCallback(
    (element: HTMLElement | null) => {
      cleanup.current?.();
      cleanup.current = undefined;
      if (!element || !active || !analytics?.enabled(key)) return;
      if (!Object.is(previous.current.identity, identity))
        previous.current = { identity, shown: false };
      if (previous.current.shown) return;
      cleanup.current = observeExposure(element, () => {
        previous.current.shown = true;
        analytics.emit(key, params ?? (() => ({}) as Params<K>));
      });
    },
    [analytics, key, identity, params, active],
  );
  return analytics?.enabled(key) && active ? ref : undefined;
}

/** Compose an internal DOM owner with the exposure observer without replacing its ref. */
export function useExposureRef<T extends HTMLElement>(
  key: ShowKey,
  target: RefObject<T | null>,
) {
  const exposure = useExposure(key);
  return useCallback(
    (node: T | null) => {
      target.current = node;
      exposure?.(node);
    },
    [target, exposure],
  );
}

/** Exit animations keep dialogs mounted; a closed target must stop observing immediately. */
export function useOpenExposure(key: ShowKey, open: boolean) {
  return useExposure(key, open, undefined, open);
}
