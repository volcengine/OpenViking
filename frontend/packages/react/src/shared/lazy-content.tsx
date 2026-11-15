import { LoadCapture } from './monitoring/load-capture.js';
import { markFault } from './monitoring/fault.js';
import { useCodeLoad, type CodeTarget } from './monitoring/use-code-load.js';
import { lazy, Suspense, useState, useCallback } from 'react';
import type { ComponentType, ReactNode } from 'react';

/** Keep a lazy type stable while mounted; a boundary retry gets a fresh loader. */
export function LazyContent<Props extends object>({
  load,
  monitoringTarget,
  contentProps,
  fallback,
}: {
  monitoringTarget?: CodeTarget;
  load: () => Promise<{ default: ComponentType<Props> }>;
  contentProps: Props;
  fallback: ReactNode;
}) {
  // This component owns Suspense, so its state commits even while View is loading.
  // The enclosing error boundary unmounts it on failure, clearing lazy's rejection.
  const checked = useCallback(async () => {
    const result = await load();
    if (!result?.default)
      throw markFault(
        new Error('Required component export is missing'),
        'configuration',
        'RESOURCE_EXPORT_INVALID',
      );
    return result;
  }, [load]);
  const observer = useCodeLoad(checked, monitoringTarget);
  const [View] = useState(() => lazy(observer.load));
  return (
    <LoadCapture caught={(error) => observer.caught(error)}>
      <Suspense fallback={fallback}>
        <View {...contentProps} />
      </Suspense>
    </LoadCapture>
  );
}
