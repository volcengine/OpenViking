import { bindClient, bindingOf } from './client.js';
import type { OvClient } from '../../client/types.js';
import type { MonitoringSpan } from './trace/span.js';

const scopedService = Symbol('OpenViking.monitoring.service');
type Scoped<T> = { [scopedService]?: (span: MonitoringSpan) => T };
export function nativeService<T extends object>(
  service: T,
  client: OvClient,
  create: (client: OvClient) => T,
): T {
  Object.defineProperty(service, scopedService, {
    value: (span: MonitoringSpan) => {
      const binding = bindingOf(client);
      return create(
        binding
          ? bindClient(client, {
              ...binding,
              active: () =>
                binding.active() && binding.controller === span.controller,
              source: () => span.node.source,
              parent: span,
            })
          : client,
      );
    },
  });
  return service;
}
export function scopeService<T extends object>(
  service: T,
  span?: MonitoringSpan,
): T {
  if (!span) return service;
  const factory = (service as Scoped<T>)[scopedService];
  if (factory) return factory(span);
  span.gap('opaque-host-service');
  return service;
}

export const isNativeService = (service: object) => scopedService in service;
