import type {
  VikingBotDraft,
  VikingBotMessage,
} from '../../../components/viking-bot/model.js';
import { steerInput } from './steer-input.js';
import type { AppendInput } from './chat-timeline.js';
import { runChat } from './chat-run.js';
import type {
  UseVikingBotChatOptions,
  VikingBotChatService,
  VikingBotChatState,
} from './types.js';

const asError = (value: unknown) =>
  value instanceof Error ? value : new Error(String(value));

export function createChatController(
  service: VikingBotChatService,
  options: UseVikingBotChatOptions,
  parentSignal?: AbortSignal,
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
  const update = (patch: Partial<VikingBotChatState>) => {
    state = { ...state, ...patch };
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
  let persistence = Promise.resolve();
  const reload = async () => {
    if (!active || !state.sessionId || request) return;
    const sessionId = state.sessionId;
    const controller = new AbortController();
    request = controller;
    const signal = signalFor(controller);
    update({ status: 'loading', error: undefined });
    try {
      await persistence;
      signal.throwIfAborted();
      const messages = await service.loadMessages(sessionId, signal);
      signal.throwIfAborted();
      if (current(controller)) update({ messages, status: 'idle' });
    } catch (error) {
      if (current(controller))
        update({
          status: signal.aborted ? 'idle' : 'error',
          error: signal.aborted ? undefined : asError(error),
        });
    } finally {
      if (current(controller)) {
        request = undefined;
        appendInput = undefined;
      }
    }
  };
  const save = (sessionId: string, messages: VikingBotMessage[]) => {
    const saveMessage = service.saveMessage;
    if (options.persist === false || !saveMessage) return;
    const signal = signalFor(new AbortController());
    persistence = persistence.then(async () => {
      try {
        for (const message of messages) {
          signal.throwIfAborted();
          await saveMessage(sessionId, message, signal);
        }
      } catch (error) {
        if (active && state.sessionId === sessionId && !signal.aborted)
          update({ persistenceError: asError(error) });
      }
    });
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
    try {
      return await runChat({
        service,
        state,
        draft,
        signal: signalFor(controller),
        current: () => current(controller),
        update,
        save,
        attach: (append) => {
          appendInput = append;
        },
      });
    } finally {
      if (current(controller)) {
        request = undefined;
        appendInput = undefined;
      }
    }
  };
  const selectSession = (sessionId?: string) => {
    cancel();
    update({
      sessionId,
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
    setMessages: (messages: readonly VikingBotMessage[]) => {
      if (!request) update({ messages });
    },
  };
}
