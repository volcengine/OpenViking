import type { MonitoringSpan } from './trace/span.js';
import { createContext, useContext, useMemo } from 'react';
import type { ReactNode } from 'react';
import type { Workflow } from './workflow.js';

const Context = createContext<
  | {
      operation?: Workflow;
      spans: readonly import('./trace/span.js').MonitoringSpan[];
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
  children,
}: {
  operation?: Workflow | readonly Workflow[];
  spans?: readonly MonitoringSpan[];
  children: ReactNode;
}) {
  const parent = useContext(Context);
  const value = useMemo(
    () =>
      spans
        ? { spans }
        : !operation
          ? parent
          : isMany(operation)
            ? { spans: operation.map((item) => item.span) }
            : {
                operation,
                spans: [operation.span],
              },
    [operation, parent, spans],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
