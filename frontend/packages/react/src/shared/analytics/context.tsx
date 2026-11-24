import {
  createContext,
  useContext,
  useInsertionEffect,
  useMemo,
  useRef,
} from 'react';
import type { ReactNode } from 'react';
import type { OpenVikingAnalyticsOptions } from '../../client/analytics/types.js';
import { createReporter } from './reporter.js';

const AnalyticsContext = createContext<OpenVikingAnalyticsOptions | false>(
  false,
);

export function AnalyticsProvider({
  analytics,
  children,
}: {
  analytics?: OpenVikingAnalyticsOptions | false;
  children?: ReactNode;
}) {
  const parent = useContext(AnalyticsContext);
  return (
    <AnalyticsContext.Provider value={analytics ?? parent}>
      {children}
    </AnalyticsContext.Provider>
  );
}

/** Private collection endpoint, shared by business components and blocks. */
export function useAnalytics() {
  const options = useContext(AnalyticsContext);
  const instance = useRef<string | undefined>(undefined);
  return useMemo(
    () => (options ? createReporter(options, instance) : undefined),
    [options],
  );
}

/** Read the committed receiver without making it a request/controller dependency. */
export function useAnalyticsRef() {
  const reporter = useAnalytics();
  const ref = useRef(reporter);
  // Commit the host's external receiver before refs and layout effects can invoke
  // actions. Render cannot update it because concurrent renders may be discarded;
  // passive effects are too late. Cleanup cuts off delivery on replacement/unmount.
  useInsertionEffect(() => {
    ref.current = reporter;
    return () => {
      ref.current = undefined;
    };
  }, [reporter]);
  return ref;
}
