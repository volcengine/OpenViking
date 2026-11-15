import { useCallback, useRef, useState } from 'react';
import { useOvIdentity } from '../../client/provider.js';
import { isOvClientError } from '../../client/errors.js';
import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { scopeKeyOf } from './fs-scope.js';
import { normalizeResourceUri, resourceUriIdentity } from './fs-uri.js';

const userRoot = /^viking:\/\/user\/[^/]+$/;
const homeAlias = 'viking://~';
type ResolvedHome = { scope: string; uri: string };

function unsupportedHomeLookup(error: unknown): boolean {
  return (
    isOvClientError(error) &&
    ((error.statusCode === 400 && error.code === 'UNSUPPORTED_URI') ||
      (error.statusCode === 501 &&
        (error.code === 'UNIMPLEMENTED' || error.code === 'HTTP_501')))
  );
}

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
      signal.throwIfAborted();
      if (configuredHome) return configuredHome;
      if (cache.current?.scope === scope) return cache.current.uri;
      if (!client) return;
      try {
        const stat = await client.request<{ uri?: unknown; isDir?: unknown }>({
          method: 'GET',
          path: OV_PATHS.fsStat,
          query: { uri: homeAlias },
          options: { ...requestOptions, signal },
        });
        signal.throwIfAborted();
        if (stat?.isDir !== true || typeof stat.uri !== 'string')
          throw new TypeError('Invalid home directory stat');
        const uri = normalizeResourceUri(stat.uri);
        // Some gateways can stat ~ but return it verbatim instead of resolving it.
        if (uri === homeAlias) return;
        if (!userRoot.test(uri))
          throw new TypeError('Invalid canonical home URI');
        const home = { scope, uri };
        cache.current = home;
        setResolved(home);
        return uri;
      } catch (error) {
        signal.throwIfAborted();
        if (unsupportedHomeLookup(error)) return;
        throw error;
      }
    },
    [client, configuredHome, requestOptions, scope],
  );
  return { identifyUri, resolveHome };
}
