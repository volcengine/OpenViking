import type { VikingBotMessage } from '../../../components/viking-bot/model.js';
import type { MonitoringBinding } from '../../../shared/monitoring/binding.js';
import type { MonitoringSpan } from '../../../shared/monitoring/trace/span.js';
import {
  scopeService,
  isNativeService,
} from '../../../shared/monitoring/service.js';
import { persistenceSpan } from './reply-monitoring.js';
import type { VikingBotChatService } from './types.js';

export function createPersistence(
  service: VikingBotChatService,
  options: {
    persist?: boolean;
    signal: () => AbortSignal;
    current: (sessionId: string) => boolean;
    failed: (error: unknown) => void;
    monitoring?: MonitoringBinding;
  },
) {
  let pending = Promise.resolve();
  return {
    get pending() {
      return pending;
    },
    save(
      sessionId: string,
      messages: VikingBotMessage[],
      parent?: MonitoringSpan,
    ) {
      if (options.persist === false || !service.saveMessage) return;
      const signal = options.signal();
      pending = pending.then(async () => {
        const span = signal.aborted
          ? undefined
          : persistenceSpan(options.monitoring, parent);
        const scoped = scopeService(service, span);
        try {
          for (const message of messages) {
            if (message.clientError) continue;
            signal.throwIfAborted();
            await scoped.saveMessage!(sessionId, message, signal);
          }
          span?.finish();
        } catch (error) {
          span?.fail(
            'console_chat_history_save_fail',
            error,
            {},
            isNativeService(service) ? 'storage' : 'host-service',
            signal,
          );
          if (options.current(sessionId) && !signal.aborted)
            options.failed(error);
        }
      });
    },
  };
}
