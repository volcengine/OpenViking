import { useCallback, useRef, useState } from 'react';
import { useOvIdentity } from '../../client/provider.js';
import { isOvClientError } from '../../client/errors.js';
import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { scopeKeyOf } from './fs-scope.js';
import {
  HOME_URI,
  normalizeResourceUri,
  resourceUriIdentity,
} from '../resource-uri.js';

const userRoot = /^viking:\/\/user\/[^/]+$/;
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
  cacheScopeKey?: string,
  requestSignal?: AbortSignal,
) {
  const { userId } = useOvIdentity(requestOptions);
  const configuredHome =
    typeof userId === 'string' && userId && !userId.includes('/')
      ? `viking://user/${userId}`
      : undefined;
  const scope = JSON.stringify([
    scopeKeyOf(client, requestOptions, rootUri),
    cacheScopeKey,
  ]);
  const cache = useRef<ResolvedHome | null>(null);
  const [resolved, setResolved] = useState<ResolvedHome | null>(null);
  const home =
    configuredHome ?? (resolved?.scope === scope ? resolved.uri : undefined);
  const identifyUri = useCallback(
    (uri: string) => resourceUriIdentity(uri, home),
    [home],
  );

  const readHome = useCallback(
    () =>
      configuredHome ??
      (cache.current?.scope === scope ? cache.current.uri : undefined),
    [configuredHome, scope],
  );

  // Resolve on demand. Ordinary canonical navigation does not need an extra
  // request, and an auth scope change cannot reuse another user's alias.
  const resolveHome = useCallback(
    async (callerSignal: AbortSignal) => {
      const signal = requestSignal
        ? AbortSignal.any([requestSignal, callerSignal])
        : callerSignal;
      signal.throwIfAborted();
      const known = readHome();
      if (known) return known;
      if (!client) return;
      try {
        const stat = await client.request<{ uri?: unknown; isDir?: unknown }>({
          method: 'GET',
          path: OV_PATHS.fsStat,
          query: { uri: HOME_URI },
          options: { ...requestOptions, signal },
        });
        signal.throwIfAborted();
        if (stat?.isDir !== true || typeof stat.uri !== 'string')
          throw new TypeError('Invalid home directory stat');
        const uri = normalizeResourceUri(stat.uri);
        // Some gateways can stat ~ but return it verbatim instead of resolving it.
        if (uri === HOME_URI) return;
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
    [client, readHome, requestOptions, requestSignal, scope],
  );
  return { identifyUri, readHome, resolveHome };
}
