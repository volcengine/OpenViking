import type { MonitoringBinding } from '../../../shared/monitoring/binding.js';
import type { MonitoringSpan } from '../../../shared/monitoring/trace/span.js';
import { workflowForBinding } from '../../../shared/monitoring/workflow.js';
import {
  scopeService,
  isNativeService,
} from '../../../shared/monitoring/service.js';
import type { VikingBotChatService, VikingBotChatState } from './types.js';

export async function loadHistory(options: {
  service: VikingBotChatService;
  monitoring?: MonitoringBinding;
  sessionId: string;
  signal: AbortSignal;
  pending: Promise<void>;
  current: () => boolean;
  update: (patch: Partial<VikingBotChatState>, span?: MonitoringSpan) => void;
}) {
  const { service, monitoring, signal, sessionId, current, update } = options;
  const operation = workflowForBinding(
    monitoring,
    'console_session_history_load',
    signal,
  );
  update({
    status: 'loading',
    historyKey: {},
    responseComplete: false,
    error: undefined,
  });
  try {
    await options.pending;
    signal.throwIfAborted();
    const messages = await scopeService(service, operation?.span).loadMessages(
      sessionId,
      signal,
    );
    signal.throwIfAborted();
    if (current()) {
      // Persisted messages do not prove that the generating stream completed normally.
      update(
        { messages, status: 'idle', responseComplete: false },
        operation?.span,
      );
      operation?.ready({ messageCount: messages.length });
    }
  } catch (error) {
    operation?.fail(
      error,
      isNativeService(service) ? 'protocol' : 'host-service',
      signal,
    );
    if (current())
      update({
        status: signal.aborted ? 'idle' : 'error',
        error: signal.aborted
          ? undefined
          : error instanceof Error
            ? error
            : new Error(String(error)),
      });
  }
}
