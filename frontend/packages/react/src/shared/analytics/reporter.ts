import type { RefObject } from 'react';
import type {
  OpenVikingAnalyticsEvent,
  OpenVikingAnalyticsEventMap,
  OpenVikingAnalyticsEventName,
  OpenVikingAnalyticsOptions,
} from '../../client/analytics/types.js';

export type EventKey =
  OpenVikingAnalyticsEventName extends `openviking_${infer K}` ? K : never;
export type Params<K extends EventKey> =
  OpenVikingAnalyticsEventMap[`openviking_${K}`];
export type ShowKey =
  Extract<EventKey, `${string}_show`> | 'directory_tour_start';

export function createReporter(
  options: OpenVikingAnalyticsOptions,
  instance: RefObject<string | undefined>,
) {
  const names = options.events && new Set(options.events);
  const enabled = (key: EventKey) => !names || names.has(`openviking_${key}`);
  return {
    enabled,
    emit<K extends EventKey>(key: K, params: () => Params<K>) {
      if (!enabled(key)) return;
      try {
        instance.current ??= createId();
        const event = {
          name: `openviking_${key}`,
          params: params(),
          instanceId: instance.current,
        } as OpenVikingAnalyticsEvent;
        void Promise.resolve(options.onEvent(event)).catch(() => {});
      } catch {
        /* A host receiver cannot change the business result. */
      }
    },
  };
}
export type AnalyticsReporter = ReturnType<typeof createReporter>;

const createId = () =>
  globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2);

/** Only created while collection is enabled, and never contains business input. */
export function startOperation() {
  const operation_id = createId();
  const start = performance.now();
  return {
    operation_id,
    finish: () => ({ operation_id, duration_ms: performance.now() - start }),
  };
}

export function errorKind(
  error: unknown,
): 'network' | 'http' | 'business' | 'permission' | 'unknown' {
  if (error instanceof Error && error.name === 'NotAllowedError')
    return 'permission';
  if (typeof error !== 'object' || !error) return 'unknown';
  const value = error as { code?: unknown; statusCode?: number };
  if (value.code === 'NETWORK_ERROR') return 'network';
  if (value.statusCode && value.statusCode >= 400) return 'http';
  return typeof value.code === 'string' ? 'business' : 'unknown';
}
