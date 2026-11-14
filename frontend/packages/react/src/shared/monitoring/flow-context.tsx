import type { MonitoringSpan } from './trace/span.js';
import { createContext, useContext, useMemo } from 'react';
import type { ReactNode } from 'react';
import type { Workflow } from './workflow.js';

const Context = createContext<
  | {
      operation?: Workflow;
      spans: readonly import('./trace/span.js').MonitoringSpan[];
      gap?: 'shared-query-input';
    }
  | undefined
>(undefined);
export const useMonitoringFlow = () => useContext(Context);
const isMany = (
  value: Workflow | readonly Workflow[],
): value is readonly Workflow[] => Array.isArray(value);
export function MonitoringFlow({
  operation,
  spans,
  unobservedInput,
  children,
}: {
  operation?: Workflow | readonly Workflow[];
  spans?: readonly MonitoringSpan[];
  unobservedInput?: boolean;
  children: ReactNode;
}) {
  const parent = useContext(Context);
  const value = useMemo(
    () =>
      unobservedInput
        ? { spans: [], gap: 'shared-query-input' as const }
        : spans
          ? { spans }
          : !operation
            ? parent
            : isMany(operation)
              ? { spans: operation.map((item) => item.span) }
              : {
                  operation,
                  spans: [operation.span],
                },
    [operation, parent, spans, unobservedInput],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
