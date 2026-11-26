import type {
  Source,
  ComponentName,
  RegionByComponent,
} from '../../client/monitoring/source.js';
import type { MonitoringController } from './controller.js';
import type { MonitoringSpan } from './trace/span.js';

export interface MonitoringBinding {
  readonly controller: MonitoringController;
  readonly contextGap?: () => 'disabled-scope' | undefined;
  readonly dataScope?: () => string | undefined;
  readonly active: () => boolean;
  readonly source: (region?: RegionByComponent[ComponentName]) => Source;
  readonly related?: readonly MonitoringSpan[];
  readonly parent?: MonitoringSpan;
}

/** Private symbol carried only by instance-bound clients, never public Props. */
export const rawClient = Symbol('OpenViking.raw-client');
export const clientBinding = Symbol('OpenViking.monitoring');
export interface BoundClient {
  readonly [rawClient]?: import('../../client/types.js').OvClient;
  readonly [clientBinding]?: MonitoringBinding;
}

/** Keep a scope's committed receiver identity, even when its frame is rebound. */
export function scopeBinding(
  binding: MonitoringBinding,
  region?: Parameters<MonitoringBinding['source']>[0],
  parent:
    | import('./trace/span.js').MonitoringSpan
    | null
    | undefined = binding.parent,
  related = binding.related,
): MonitoringBinding {
  const controller = binding.controller;
  return {
    controller,
    dataScope: binding.dataScope,
    contextGap: binding.contextGap,
    active: () => binding.active() && binding.controller === controller,
    source: () => binding.source(region),
    parent: parent ?? undefined,
    related,
  };
}
