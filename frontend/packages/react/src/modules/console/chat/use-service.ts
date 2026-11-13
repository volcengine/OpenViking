import { useMemo, useState } from 'react';
import { createOvClient } from '../../../client/client.js';
import { useOvClient, useOvSignal } from '../../../client/provider.js';
import type { VikingBotHookOptions } from './types.js';
import { createNativeBotService } from './native-service.js';

/** Reuse Provider identity resolution; a locale change never replaces the service. */
export function useBotService({
  requestOptions = {},
  scopeKey,
}: VikingBotHookOptions) {
  const inherited = useOvClient();
  const inheritedSignal = useOvSignal();
  const [fallback] = useState(() => createOvClient());
  const client = inherited ?? fallback;
  const {
    baseURL,
    apiKey,
    accountId,
    userId,
    resourceId,
    signal = inheritedSignal,
  } = requestOptions;
  const service = useMemo(() => {
    void scopeKey;
    return createNativeBotService(client, {
      baseURL,
      apiKey,
      accountId,
      userId,
      resourceId,
      signal,
    });
  }, [
    client,
    baseURL,
    apiKey,
    accountId,
    userId,
    resourceId,
    signal,
    scopeKey,
  ]);
  return { service, signal };
}
