import { useCallback, useRef, useState } from 'react';
import { useOvIdentity } from '../../client/provider.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { resolveDashboardUri } from './dashboard-resource.js';
import { scopeKeyOf } from './fs-scope.js';
import { normalizeResourceUri, resourceUriIdentity } from './fs-uri.js';

const userRoot = /^viking:\/\/user\/[^/]+$/;
type ResolvedHome = { scope: string; uri: string };

export function useResourceUriIdentity(
  client: OvClient | null,
  requestOptions: OvRequestOptions | undefined,
  rootUri: string,
) {
  const { userId } = useOvIdentity(requestOptions);
  const configuredHome =
    typeof userId === 'string' && userId && !userId.includes('/')
      ? `viking://user/${userId}`
      : undefined;
  const scope = scopeKeyOf(client, requestOptions, rootUri);
  const cache = useRef<ResolvedHome | null>(null);
  const [resolved, setResolved] = useState<ResolvedHome | null>(null);
  const home =
    configuredHome ?? (resolved?.scope === scope ? resolved.uri : undefined);
  const identifyUri = useCallback(
    (uri: string) => resourceUriIdentity(uri, home),
    [home],
  );

  // Resolve on demand. Ordinary canonical navigation does not need an extra
  // request, and an auth scope change cannot reuse another user's alias.
  const resolveHome = useCallback(
    async (signal: AbortSignal) => {
      if (signal.aborted) return;
      if (configuredHome) return configuredHome;
      if (cache.current?.scope === scope) return cache.current.uri;
      if (!client) return;
      try {
        const uri = normalizeResourceUri(
          await resolveDashboardUri(client, 'viking://~', {
            ...requestOptions,
            signal: requestOptions?.signal
              ? AbortSignal.any([requestOptions.signal, signal])
              : signal,
          }),
        );
        if (
          signal.aborted ||
          requestOptions?.signal?.aborted ||
          !userRoot.test(uri)
        )
          return;
        const home = { scope, uri };
        cache.current = home;
        setResolved(home);
        return uri;
      } catch {
        // Some gateways cannot resolve the alias. Compare the original
        // spelling in that case rather than guessing a user id.
        return undefined;
      }
    },
    [client, configuredHome, requestOptions, scope],
  );
  return { identifyUri, resolveHome };
}
