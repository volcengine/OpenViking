import { useEffect, useMemo, useState } from 'react';
import { createOvClient } from '../../../client/client.js';
import { useOvClient, useOvIdentity } from '../../../client/provider.js';
import type { OvRequestOptions } from '../../../client/types.js';
import type { VikingBotChatResult } from '../chat/types.js';

export function useHitlScope(
  chat: VikingBotChatResult,
  requestOptions: OvRequestOptions | undefined,
  customChat: boolean | undefined,
) {
  const inheritedClient = useOvClient('local');
  const [fallback] = useState(createOvClient);
  const user = useOvIdentity(requestOptions).userId;
  const userId = typeof user === 'string' ? user : undefined;
  const hasConnection =
    !customChat ||
    requestOptions?.baseURL !== undefined ||
    requestOptions?.apiKey !== undefined ||
    requestOptions?.accountId !== undefined ||
    requestOptions?.userId !== undefined ||
    requestOptions?.resourceId !== undefined;
  const client = inheritedClient ?? (hasConnection ? fallback : undefined);
  const { send, sessionId, historyKey } = chat;
  const owner = useMemo(
    () => ({ active: true, locked: false, send, sessionId, historyKey }),
    [send, sessionId, historyKey],
  );
  // Invalidate delivery callbacks when their conversation or provider unmounts.
  // External sends may finish after the next conversation is rendered.
  useEffect(() => {
    owner.active = true;
    return () => {
      owner.active = false;
    };
  }, [owner]);
  return { client, owner, userId };
}
