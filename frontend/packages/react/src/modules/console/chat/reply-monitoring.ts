import { readData } from '../../../shared/monitoring/data.js';
import { scopeBinding } from '../../../shared/monitoring/binding.js';
import type { MonitoringBinding } from '../../../shared/monitoring/binding.js';
import {
  startSpan,
  type MonitoringSpan,
} from '../../../shared/monitoring/trace/span.js';
import {
  scopeService,
  isNativeService,
} from '../../../shared/monitoring/service.js';
import type { VikingBotChatService, VikingBotChatEvent } from './types.js';

export function observeReply(
  binding: MonitoringBinding | undefined,
  service: VikingBotChatService,
  signal: AbortSignal,
  history?: object,
) {
  if (!binding || signal.aborted) return;
  const span = startSpan(
    scopeBinding(binding, 'chat'),
    'console-chat-reply',
    'submit',
  );
  if (!span) return;
  for (const input of readData(binding, [history]))
    span.link(input, 'render-input');
  let first = false;
  const abort = () => span.finish('cancelled', 'user-cancel');
  signal.addEventListener('abort', abort, { once: true });
  const cleanup = () => signal.removeEventListener('abort', abort);
  return {
    span,
    service: scopeService(service, span),
    first(event: VikingBotChatEvent) {
      if (first) return;
      const contentKind =
        event.type === 'tool'
          ? 'tool'
          : event.type === 'reasoning'
            ? 'reasoning'
            : 'text';
      first = true;
      span.emit('console_chat_first_content_ready', {
        ...span.ready(),
        contentKind,
      });
    },
    finish(interrupted: boolean) {
      cleanup();
      if (interrupted) span.finish('cancelled', 'interrupted');
      else if (span.finish())
        span.emit('console_chat_reply_complete', {
          ...span.ready(),
          firstContentObserved: first,
        });
    },
    fail(error: unknown, renderer = false) {
      cleanup();
      span.fail(
        'console_chat_reply_fail',
        error,
        {},
        renderer
          ? 'renderer'
          : isNativeService(service)
            ? 'unknown'
            : 'host-service',
        signal,
      );
    },
    cancel() {
      cleanup();
      span.finish('cancelled', 'superseded');
    },
  };
}
export type ReplyObservation = ReturnType<typeof observeReply>;
export function persistenceSpan(
  binding: MonitoringBinding | undefined,
  parent?: MonitoringSpan,
) {
  return (
    binding &&
    startSpan(scopeBinding(binding, 'persistence', parent), 'reply-persistence')
  );
}
