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
  const { service: native, signal } = useBotService(options);
  const locale = useLocale(options.locale);
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
  ]);
  // Effect necessity: history requests and live Bot streams belong to this
  // session/identity. Events cannot observe navigation or unmount; cleanup aborts
  // the old owner and prevents late responses from updating its replacement.
  useEffect(() => controller.start(), [controller]);
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    controller.getSnapshot,
  );
  return {
    ...state,
    canInterrupt: Boolean(service.interrupt),
    canSteer: Boolean(service.steer),
    error: localizeBotError(state.error, locale),
    send: controller.send,
    abort: controller.abort,
    reset: controller.reset,
    reload: controller.reload,
    setMessages: controller.setMessages,
    selectSession: controller.selectSession,
  };
}
