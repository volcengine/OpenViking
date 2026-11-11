import { useEffect, useState } from 'react';
import type { DirectoryTourAnchor, DirectoryTourProps } from './types.js';

// Array.isArray is realm-independent; the explicit predicate also narrows the
// readonly array branch of the public anchor union.
function isAnchorGroup(
  target: DirectoryTourAnchor,
): target is readonly HTMLElement[] {
  return Array.isArray(target);
}

export function useTourAnchor(
  target: string,
  locateTarget: DirectoryTourProps['locateTarget'],
  timeout: number,
  attempt: number,
) {
  const [state, setState] = useState<{
    anchor: HTMLElement | null;
    elements: readonly HTMLElement[];
    failed: boolean;
  }>({ anchor: null, elements: [], failed: false });
  // Synchronize with the host's asynchronously rendered DOM anchor, which is not
  // available during render. Abort and ignore stale resolutions on retry/unmount;
  // the deadline also bounds hosts that never settle or ignore cancellation.
  useEffect(() => {
    const controller = new AbortController();
    setState({ anchor: null, elements: [], failed: false });
    const timer = window.setTimeout(() => {
      controller.abort();
      setState({ anchor: null, elements: [], failed: true });
    }, timeout);
    void Promise.resolve()
      .then(() => locateTarget?.(target, controller.signal) ?? null)
      .then(
        (target: DirectoryTourAnchor | null) => {
          if (controller.signal.aborted) return;
          window.clearTimeout(timer);
          const elements = target
            ? isAnchorGroup(target)
              ? target
              : [target]
            : [];
          const anchor = elements[0];
          if (anchor?.isConnected) {
            anchor.scrollIntoView({ block: 'nearest', inline: 'nearest' });
            setState({ anchor, elements, failed: false });
          } else {
            setState({ anchor: null, elements: [], failed: true });
          }
        },
        () => {
          if (controller.signal.aborted) return;
          window.clearTimeout(timer);
          setState({ anchor: null, elements: [], failed: true });
        },
      );
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [target, locateTarget, timeout, attempt]);
  return state;
}
