import type {
  VikingBotDraft,
  VikingBotMessage,
} from '../../components/viking-bot/model.js';
import { createChatTimeline, type AppendInput } from './chat-timeline.js';
import { OvClientError } from '../../client/errors.js';
import type { VikingBotChatService, VikingBotChatState } from './types.js';

/** One request owns its accumulator and snapshot, never a global conversation. */
export async function runChat({
  service,
  state,
  draft,
  signal,
  current,
  update,
  save,
  attach,
}: {
  service: VikingBotChatService;
  state: VikingBotChatState;
  draft: VikingBotDraft;
  signal: AbortSignal;
  current: () => boolean;
  update: (patch: Partial<VikingBotChatState>) => void;
  attach: (append: AppendInput) => void;
  save: (sessionId: string, messages: VikingBotMessage[]) => void;
}) {
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
  let complete = false;
  let interrupted = false;
  try {
    signal.throwIfAborted();
    if (!sessionId) {
      const session = await service.createSession(signal);
      if (!current()) return false;
      sessionId = session.id;
      update({ sessionId });
    }
    for await (const event of service.stream({ sessionId, ...draft }, signal)) {
      signal.throwIfAborted();
      if (!current()) return false;
      await timeline.apply(event);
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
    const finished = await timeline.finish();
    if (!current()) return false;
    update({
      messages: finished.messages,
      streaming: undefined,
      status: 'idle',
      isSubmitting: false,
    });
    save(sessionId, finished.added);
    return !interrupted;
  } catch (error) {
    if (!current()) return false;
    const finished = await timeline.finish();
    if (!current()) return false;
    update({
      messages: finished.messages,
      streaming: undefined,
      status: signal.aborted ? 'idle' : 'error',
      isSubmitting: false,
      ...(!signal.aborted
        ? { error: error instanceof Error ? error : new Error(String(error)) }
        : {}),
    });
    if (signal.aborted && sessionId) save(sessionId, finished.added);
    return false;
  }
}
