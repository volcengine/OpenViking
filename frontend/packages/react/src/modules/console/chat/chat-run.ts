import type { ReplyObservation } from './reply-monitoring.js';
import {
  startOperation,
  errorKind,
} from '../../../shared/analytics/reporter.js';
import type {
  VikingBotDraft,
  VikingBotMessage,
} from '../../../components/viking-bot/model.js';
import { createChatTimeline, type AppendInput } from './chat-timeline.js';
import { OvClientError } from '../../../client/errors.js';
import { recoverDraft } from './recover-draft.js';
import type { VikingBotChatService, VikingBotChatState } from './types.js';

/** One request owns its accumulator and snapshot, never a global conversation. */
export async function runChat({
  service,
  analytics,
  monitoring,
  state,
  draft,
  undelivered,
  signal,
  current,
  update,
  save,
  attach,
}: {
  service: VikingBotChatService;
  monitoring?: ReplyObservation;
  analytics?: import('react').RefObject<
    | import('../../../shared/analytics/reporter.js').AnalyticsReporter
    | undefined
  >;
  state: VikingBotChatState;
  draft: VikingBotDraft;
  undelivered: VikingBotDraft[];
  signal: AbortSignal;
  current: () => boolean;
  update: (patch: Partial<VikingBotChatState>) => void;
  attach: (append: AppendInput) => void;
  save: (sessionId: string, messages: VikingBotMessage[]) => void;
}) {
  const operation = analytics?.current && startOperation();
  if (operation)
    analytics?.current?.emit('viking_bot_message_submit', () => ({
      operation_id: operation.operation_id,
    }));
  const timeline = createChatTimeline(state.messages, draft, current, update);
  attach(timeline.append);
  update({
    status: 'streaming',
    error: undefined,
    persistenceError: undefined,
    isSubmitting: false,
  });
  timeline.publish();
  let sessionId = state.sessionId;
  const recoveredDraft = recoverDraft(undelivered, draft);
  const recovered = sessionId
    ? []
    : state.messages.filter((message) => message.role === 'user');
  let started = false;
  let complete = false;
  let interrupted = false;
  let delivered = false;
  let renderer = false;
  let stage:
    | import('../../../shared/monitoring/trace/span.js').MonitoringSpan
    | undefined;
  try {
    signal.throwIfAborted();
    if (!sessionId) {
      const session = await service.createSession(signal);
      if (!current()) return false;
      sessionId = session.id;
      update({ sessionId });
      save(sessionId, recovered);
    }
    for await (const event of service.stream(
      { sessionId, ...recoveredDraft },
      signal,
    )) {
      if (!current()) return false;
      // A stream event acknowledges delivery; creating a session does not.
      delivered = true;
      undelivered.length = 0;
      signal.throwIfAborted();
      if (
        !started &&
        (((event.type === 'text' || event.type === 'reasoning') &&
          event.text.length > 0) ||
          event.type === 'tool')
      ) {
        started = true;
        monitoring?.first(event);
        if (operation)
          analytics?.current?.emit('viking_bot_reply_start', operation.finish);
      }
      if (!started && event.type === 'complete' && event.text) {
        started = true;
        monitoring?.first(event);
        if (operation)
          analytics?.current?.emit('viking_bot_reply_start', operation.finish);
      }
      renderer = true;
      stage = monitoring?.span.child('normalize-response');
      await timeline.apply(event);
      stage?.finish();
      renderer = false;
      signal.throwIfAborted();
      if (event.type === 'complete' || event.type === 'interrupted') {
        complete = true;
        interrupted = event.type === 'interrupted';
        break;
      }
    }
    if (!current()) return false;
    signal.throwIfAborted();
    if (!complete)
      throw new OvClientError({
        code: 'BOT_STREAM_TRUNCATED',
        message: 'VikingBot stream ended before a terminal event',
      });
    renderer = true;
    stage = monitoring?.span.child('view-commit');
    const finished = await timeline.finish();
    if (!current()) return false;
    update({
      messages: finished.messages,
      streaming: undefined,
      status: 'idle',
      isSubmitting: false,
    });
    stage?.finish();
    monitoring?.finish(interrupted);
    if (operation)
      analytics?.current?.emit(
        interrupted ? 'viking_bot_reply_cancel' : 'viking_bot_reply_success',
        operation.finish,
      );
    save(sessionId, finished.added);
    return !interrupted;
  } catch (error) {
    stage?.finish(signal.aborted ? 'cancelled' : 'failed');
    monitoring?.fail(error, renderer);
    if (!current()) return false;
    if (operation) {
      if (signal.aborted)
        analytics?.current?.emit('viking_bot_reply_cancel', operation.finish);
      else
        analytics?.current?.emit('viking_bot_reply_fail', () => ({
          ...operation.finish(),
          error_kind: errorKind(error),
        }));
    }
    if (!delivered) undelivered.push(draft);
    const finished = await timeline.finish(signal.aborted);
    if (!current()) return false;
    const failure = error instanceof Error ? error : new Error(String(error));
    const messages = signal.aborted
      ? finished.messages
      : [
          ...finished.messages,
          {
            id: globalThis.crypto.randomUUID(),
            turnId: finished.messages.at(-1)?.turnId,
            role: 'system' as const,
            tone: 'error' as const,
            parts: [{ type: 'text' as const, text: failure.message }],
            clientError: failure,
          },
        ];
    update({
      messages,
      streaming: undefined,
      status: signal.aborted ? 'idle' : 'error',
      isSubmitting: false,
      ...(!signal.aborted ? { error: failure } : {}),
    });
    // Preserve completed segments, accepted inputs without presenting an unfinished reply as complete history.
    if (sessionId) save(sessionId, finished.added);
    return false;
  }
}
