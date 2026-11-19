import { useMemo } from 'react';
import { useBotService } from './chat/use-service.js';
import type { ConsoleProps } from './types.js';

/** Follow the chat service's request identity while terminal state stays local. */
export function useConversationDraftIdentity({
  chatOptions,
  requestOptions,
  scopeKey,
}: Pick<ConsoleProps, 'chatOptions' | 'requestOptions' | 'scopeKey'>) {
  const { service: native } = useBotService({
    requestOptions: chatOptions?.requestOptions ?? requestOptions,
    scopeKey: chatOptions?.scopeKey ?? scopeKey,
  });
  const activation = chatOptions?.activation;
  const service = chatOptions?.service;
  const persist = chatOptions?.persist;
  return useMemo(
    () => ({ native, activation, service, persist }),
    [native, activation, service, persist],
  );
}
