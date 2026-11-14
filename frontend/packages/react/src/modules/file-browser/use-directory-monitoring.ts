import { useLayoutEffect, useMemo } from 'react';
import type { DirState } from './fs-model.js';

/** Observe committed directory state; never use a fetch callback as UI readiness. */
export function useDirectoryMonitoring(
  dirs: Record<string, DirState>,
  ready: boolean,
) {
  const operations = useMemo(
    () => Object.values(dirs).flatMap((dir) => dir.monitoring ?? []),
    [dirs],
  );
  useLayoutEffect(() => {
    if (!ready) return;
    for (const dir of Object.values(dirs)) {
      if (dir.loading || dir.error) continue;
      for (const operation of dir.monitoring ?? [])
        operation.ready({
          entryCount: dir.entries.length,
          empty: dir.entries.length === 0,
        });
    }
  }, [dirs, ready]);
  return operations;
}
