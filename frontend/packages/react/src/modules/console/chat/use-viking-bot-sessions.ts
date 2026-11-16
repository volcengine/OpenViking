import { useMonitoringBinding } from '../../../shared/monitoring/context.js';
import { useEffect, useMemo, useSyncExternalStore } from 'react';
import { createSessionsController } from './sessions-controller.js';
import { useBotService } from './use-service.js';
import type {
  UseVikingBotSessionsOptions,
  VikingBotSessionsResult,
} from './types.js';

export function useVikingBotSessions(
  options: UseVikingBotSessionsOptions = {},
): VikingBotSessionsResult {
  const monitoring = useMonitoringBinding();
  const { service: native, signal } = useBotService(options);
  const { service = native, enabled = true, scopeKey } = options;
  const controller = useMemo(() => {
    void scopeKey;
    void native;
    return createSessionsController(service, enabled, signal, monitoring);
  }, [native, service, enabled, signal, scopeKey, monitoring]);
  // Effect necessity: the session list is synchronized with a remote service.
  // Identity changes can arrive through navigation, not just clicks. Cleanup
  // cancels this instance's requests and invalidates any late list/mutation result.
  useEffect(() => controller.start(), [controller]);
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
  );
  return {
    ...state,
    refresh: controller.refresh,
    create: controller.create,
    remove: controller.remove,
  };
}
