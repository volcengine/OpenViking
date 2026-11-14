import { createContext, useContext, useInsertionEffect, useState } from 'react';
import type { ReactNode } from 'react';
import type { OpenVikingMonitoringOptions } from '../../client/monitoring/types.js';
import { MonitoringController } from './controller.js';
import type { MonitoringBinding } from './binding.js';

const Rendered = createContext(false);
export const useRenderedMonitoring = () => useContext(Rendered);
const Context = createContext<MonitoringController | undefined>(undefined);
export const OwnerContext = createContext<MonitoringBinding | undefined>(
  undefined,
);
export const useMonitoringBinding = () => useContext(OwnerContext);
export const useMonitoringController = () => useContext(Context);

export function MonitoringProvider({
  monitoring,
  children,
}: {
  monitoring?: OpenVikingMonitoringOptions | false;
  children?: ReactNode;
}) {
  const parent = useContext(Context);
  const inheritedEnabled = useContext(Rendered);
  const [own] = useState(() => new MonitoringController());
  const controller = monitoring === undefined && parent ? parent : own;
  // Commit the external receiver before ref callbacks and layout effects. A
  // discarded concurrent render must never publish a receiver or enable tracing.
  useInsertionEffect(() => {
    if (controller === own) own.configure(monitoring ?? false);
  }, [own, controller, monitoring]);
  useInsertionEffect(
    () => () => {
      if (controller === own) own.configure(false);
    },
    [own, controller],
  );
  const enabled =
    monitoring === undefined
      ? inheritedEnabled
      : !!monitoring && monitoring.events?.length !== 0;
  return (
    <Context.Provider value={controller}>
      <Rendered.Provider value={enabled}>{children}</Rendered.Provider>
    </Context.Provider>
  );
}
