import { useEffect, useState } from 'react';
import type { DirectoryTourProps } from './types.js';

export function useTourAnchor(
  target: string,
  locateTarget: DirectoryTourProps['locateTarget'],
  timeout: number,
) {
  const [state, setState] = useState<{
    anchor: HTMLElement | null;
    failed: boolean;
  }>({ anchor: null, failed: false });
  // Synchronize with the host's asynchronously rendered DOM anchor, which is not
  // available during render. Abort and ignore stale resolutions on target changes/unmount;
  // the deadline also bounds hosts that never settle or ignore cancellation.
  useEffect(() => {
    const controller = new AbortController();
    setState({ anchor: null, failed: false });
    const timer = window.setTimeout(() => {
      controller.abort();
      setState({ anchor: null, failed: true });
    }, timeout);
    void Promise.resolve()
      .then(() => locateTarget?.(target, controller.signal) ?? null)
      .then(
        (anchor) => {
          if (controller.signal.aborted) return;
          window.clearTimeout(timer);
          if (anchor?.isConnected) {
            anchor.scrollIntoView({ block: 'nearest', inline: 'nearest' });
            setState({ anchor, failed: false });
          } else {
            setState({ anchor: null, failed: true });
          }
        },
        () => {
          if (controller.signal.aborted) return;
          window.clearTimeout(timer);
          setState({ anchor: null, failed: true });
        },
      );
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [target, locateTarget, timeout]);
  return state;
}
