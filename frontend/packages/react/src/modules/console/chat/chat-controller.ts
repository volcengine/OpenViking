import { asBotError as asError } from './errors.js';
import { loadHistory } from './chat-history.js';
import { createPersistence } from './chat-persistence.js';
import { tagData, readData } from '../../../shared/monitoring/data.js';
import type { MonitoringBinding } from '../../../shared/monitoring/binding.js';
import type { MonitoringSpan } from '../../../shared/monitoring/trace/span.js';
import { observeReply } from './reply-monitoring.js';
import type { VikingBotDraft } from '../../../components/viking-bot/model.js';
import { steerInput } from './steer-input.js';
import type { AppendInput } from './chat-timeline.js';
import { runChat } from './chat-run.js';
import type {
  UseVikingBotChatOptions,
  VikingBotChatService,
  VikingBotChatState,
} from './types.js';

export function createChatController(
  service: VikingBotChatService,
  options: UseVikingBotChatOptions,
  parentSignal?: AbortSignal,
  analytics?: import('react').RefObject<
    | import('../../../shared/analytics/reporter.js').AnalyticsReporter
    | undefined
  >,
  monitoring?: MonitoringBinding,
) {
  let state: VikingBotChatState = {
    sessionId: options.sessionId,
    messages: options.initialMessages ?? [],
    status: 'idle',
    isSubmitting: false,
  };
  const listeners = new Set<() => void>();
  let active = false;
  let request: AbortController | undefined;
  let appendInput: AppendInput | undefined;
  let lifetime = new AbortController();
  const undelivered: VikingBotDraft[] = [];
  const update = (
    patch: Partial<VikingBotChatState>,
    span?: MonitoringSpan,
  ) => {
    state = { ...state, ...patch };
    if (span) {
      if ('messages' in patch) tagData(monitoring, state.messages, [span]);
      if ('streaming' in patch) tagData(monitoring, state.streaming, [span]);
    }
    listeners.forEach((listener) => listener());
  };
  const cancel = () => {
    request?.abort();
    request = undefined;
    appendInput = undefined;
  };
  const signalFor = (controller: AbortController) =>
    AbortSignal.any([
      controller.signal,
      lifetime.signal,
      ...(parentSignal ? [parentSignal] : []),
    ]);
  const current = (controller: AbortController) =>
    active && request === controller;
  const persistence = createPersistence(service, {
    persist: options.persist,
    monitoring,
    signal: () => signalFor(new AbortController()),
    current: (id) => active && state.sessionId === id,
    failed: (error) => update({ persistenceError: asError(error) }),
  });
  const reload = async () => {
    if (!active || !state.sessionId || request) return;
    const sessionId = state.sessionId;
    const controller = new AbortController();
    request = controller;
    try {
      await loadHistory({
        service,
        monitoring,
        sessionId,
        signal: signalFor(controller),
        pending: persistence.pending,
        current: () => current(controller),
        update,
      });
    } finally {
      if (current(controller)) request = undefined;
    }
  };
  const send = async (input: string | VikingBotDraft) => {
    const draft =
      typeof input === 'string' ? { text: input, attachments: [] } : input;
    if (
      !active ||
      (!draft.text.trim() &&
        !draft.attachments.length &&
        !draft.references?.length)
    )
      return false;
    if (request) {
      const controller = request;
      return steerInput({
        service,
        sessionId: state.sessionId,
        draft,
        append: appendInput,
        signal: signalFor(controller),
        current: () => current(controller),
        update,
        submitting: state.isSubmitting,
      });
    }
    const controller = new AbortController();
    request = controller;
    const reply = observeReply(monitoring, service, signalFor(controller));
    try {
      return await runChat({
        service: reply?.service ?? service,
        monitoring: reply,
        analytics,
        state,
        draft,
        undelivered,
        signal: signalFor(controller),
        current: () => current(controller),
        update: (patch) => update(patch, reply?.span),
        save: (id, messages) => persistence.save(id, messages, reply?.span),
        attach: (append) => {
          appendInput = append;
        },
      });
    } finally {
      reply?.cancel();
      if (current(controller)) {
        request = undefined;
        appendInput = undefined;
      }
    }
  };
  const selectSession = (sessionId?: string) => {
    cancel();
    undelivered.length = 0;
    update({
      sessionId,
      responseComplete: false,
      messages: [],
      streaming: undefined,
      status: 'idle',
      isSubmitting: false,
      error: undefined,
      persistenceError: undefined,
    });
    if (sessionId) void reload();
  };
  return {
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    getSnapshot: () => state,
    monitoringOf: (snapshot: VikingBotChatState) =>
      readData(monitoring, [snapshot.streaming, snapshot.messages])[0],
    start() {
      active = options.enabled !== false;
      lifetime = new AbortController();
      if (active && options.initialMessages === undefined) void reload();
      return () => {
        active = false;
        cancel();
        lifetime.abort();
      };
    },
    send,
    reload,
    abort: async () => {
      const controller = request;
      const id = state.sessionId;
      if (!controller) return;
      try {
        if (id && service.interrupt && state.status === 'streaming')
          await service.interrupt(id, signalFor(controller));
      } catch (error) {
        if (current(controller)) update({ error: asError(error) });
      } finally {
        if (current(controller)) controller.abort();
      }
    },
    selectSession,
    reset: () => selectSession(),
    setMessages: (messages: VikingBotChatState['messages']) => {
      if (!request) {
        undelivered.length = 0;
        update({ messages });
      }
    },
  };
}
