import { lazy, Suspense, useState } from 'react';
import type { ComponentType, ReactNode } from 'react';

/** Keep a lazy type stable while mounted; a boundary retry gets a fresh loader. */
export function LazyContent<Props extends object>({
  load,
  contentProps,
  fallback,
}: {
  load: () => Promise<{ default: ComponentType<Props> }>;
  contentProps: Props;
  fallback: ReactNode;
}) {
  // This component owns Suspense, so its state commits even while View is loading.
  // The enclosing error boundary unmounts it on failure, clearing lazy's rejection.
  const [View] = useState(() => lazy(load));
  return (
    <Suspense fallback={fallback}>
      <View {...contentProps} />
    </Suspense>
  );
}
