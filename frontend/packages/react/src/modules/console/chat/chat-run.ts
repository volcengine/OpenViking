import type {
  VikingBotDraft,
  VikingBotMessage,
} from '../../../components/viking-bot/model.js';
import { createChatTimeline, type AppendInput } from './chat-timeline.js';
import { OvClientError } from '../../../client/errors.js';
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
    // 会话创建失败时流式请求尚未启动，这条提问没有送达服务端。撤回临时气泡，
    // 否则原文重试会在列表里留下两条相同的提问；输入内容由宿主恢复到草稿。
    //
    // A failed createSession means the stream never started and the question
    // never reached the server. Withdraw the optimistic bubble, otherwise an
    // identical retry leaves two copies in the list; the host restores the draft.
    if (!sessionId && !signal.aborted) {
      update({
        messages: state.messages,
        streaming: undefined,
        status: 'error',
        isSubmitting: false,
        error: error instanceof Error ? error : new Error(String(error)),
      });
      return false;
    }
    const finished = await timeline.finish(signal.aborted);
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
    // Preserve completed segments, accepted inputs without presenting an unfinished reply as complete history.
    if (sessionId) save(sessionId, finished.added);
    return false;
  }
}
