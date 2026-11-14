import { useMonitoringBinding } from '../../../shared/monitoring/context.js';
import { tagData, readData } from '../../../shared/monitoring/data.js';
import { useAnalyticsRef } from '../../../shared/analytics/context.js';
import { useTaskTracking } from '../../../shared/task-tracking/context.js';
import { projectBotTasks, submittedBotTasks } from './task-messages.js';
import { useEffect, useMemo, useSyncExternalStore } from 'react';
import { createChatController } from './chat-controller.js';
import { useBotService } from './use-service.js';
import { useLocale } from '../../../shared/locale-context.js';
import { localizeBotError } from './errors.js';
import type { UseVikingBotChatOptions, VikingBotChatResult } from './types.js';

/** Headless chat with instance-owned requests; output feeds VikingBot directly. */
export function useVikingBotChat(
  options: UseVikingBotChatOptions = {},
): VikingBotChatResult {
  const monitoring = useMonitoringBinding();
  const analytics = useAnalyticsRef();
  const { service: native, signal } = useBotService(options);
  const locale = useLocale(options.locale);
  const { track, tasks, source } = useTaskTracking(options);
  const {
    service = native,
    sessionId,
    enabled,
    initialMessages,
    persist,
    scopeKey,
  } = options;
  const controller = useMemo(() => {
    void native;
    return createChatController(
      service,
      {
        sessionId,
        enabled,
        initialMessages,
        persist,
        scopeKey,
      },
      signal,
      analytics,
      monitoring,
    );
  }, [
    native,
    service,
    sessionId,
    enabled,
    initialMessages,
    persist,
    signal,
    scopeKey,
    analytics,
    monitoring,
  ]);
  // Effect necessity: history requests and live Bot streams belong to this
  // session/identity. Events cannot observe navigation or unmount; cleanup aborts
  // the old owner and prevents late responses from updating its replacement.
  useEffect(() => controller.start(), [controller]);
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
  );
  // Effect necessity: register tasks from the external chat controller's stream
  // and history events, which also arrive without a user submission here.
  // Subscribe directly rather than mirror React message state; cleanup detaches
  // the old controller, and useTaskTracking releases/aborts its query source.
  useEffect(() => {
    if (enabled === false) return;
    const register = () => {
      const current = controller.getSnapshot();
      for (const task of submittedBotTasks(current.messages, current.streaming))
        track(
          task,
          controller.monitoringOf(current)?.node.stage === 'console-chat-reply'
            ? controller.monitoringOf(current)
            : undefined,
        );
    };
    register();
    return controller.subscribe(register);
  }, [controller, track, enabled]);
  const projected = useMemo(
    () => projectBotTasks(state.messages, state.streaming, tasks, source.key),
    [state.messages, state.streaming, tasks, source],
  );
  const error = localizeBotError(state.error, locale);
  const messages = projected.messages.map((message) => {
    if (!message.clientError) return message;
    const clientError =
      message.clientError === state.error
        ? error!
        : localizeBotError(message.clientError, locale)!;
    return {
      ...message,
      clientError,
      parts: [{ type: 'text' as const, text: clientError.message }],
    };
  });
  tagData(monitoring, messages, readData(monitoring, [state.messages]));
  tagData(
    monitoring,
    projected.streaming,
    readData(monitoring, [state.streaming]),
  );
  return {
    ...state,
    ...projected,
    signal,
    messages,
    canInterrupt: Boolean(service.interrupt),
    canSteer: Boolean(service.steer),
    error,
    send: controller.send,
    abort: controller.abort,
    reset: controller.reset,
    reload: controller.reload,
    setMessages: controller.setMessages,
    selectSession: controller.selectSession,
  };
}
