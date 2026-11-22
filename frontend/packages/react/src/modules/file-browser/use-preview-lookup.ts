import { useLayoutEffect, useMemo, useRef } from 'react';

import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { scopeKeyOf } from '../../shared/request/fs-scope.js';
import { createPreviewBodyCache } from './preview-body-cache.js';
import type { useWikiSaveFeedback } from './use-wiki-save-feedback.js';
import { readPreviewStat } from './fs-stat.js';
import { isMemoryUri } from './fs-memory-fields.js';
import { hasDynamicIdentity } from '../../client/managed-client.js';

export function usePreviewLookup({
  client,
  uri,
  requestOptions,
  generation,
  saveGeneration,
  wikiSave,
  listedUpdatedAt,
}: {
  client: OvClient | null;
  uri: string | null;
  requestOptions?: OvRequestOptions;
  generation: number;
  saveGeneration: number;
  wikiSave: ReturnType<typeof useWikiSaveFeedback>[1];
  listedUpdatedAt?: string;
}) {
  const scope = scopeKeyOf(client, requestOptions);
  const signal = requestOptions?.signal;
  const cacheableIdentity = client
    ? !hasDynamicIdentity(client, requestOptions)
    : false;
  const cache = useMemo(
    () => ({
      scope,
      signal,
      generation,
      saveGeneration,
      body: createPreviewBodyCache(),
    }),
    [scope, signal, generation, saveGeneration],
  );
  const lastRead = useRef(cache);
  const listed = useRef(listedUpdatedAt);
  // Preview request effects read the committed directory metadata when they
  // start. Publishing a late listing date must not initiate a new body read.
  useLayoutEffect(() => {
    listed.current = listedUpdatedAt;
  }, [listedUpdatedAt]);

  return useMemo(() => {
    const canCacheBody = (target: string) =>
      isMemoryUri(target) && /\.md$/i.test(target) && cacheableIdentity;
    const bodyCacheFor = (target: string, updatedAt?: string) =>
      canCacheBody(target) && updatedAt !== undefined
        ? cache.body.forFile(target, updatedAt)
        : undefined;
    return {
      client,
      uri,
      wikiSave,
      bodyCacheFor,
      prepareFileRead(
        api: OvClient,
        target: string,
        options: OvRequestOptions,
        unknownType: boolean,
      ) {
        const previous = lastRead.current;
        lastRead.current = cache;
        const updatedAt = listed.current;
        const reload =
          previous.scope === cache.scope &&
          (previous.generation !== cache.generation ||
            previous.saveGeneration !== cache.saveGeneration);
        const metadata =
          unknownType || reload || !updatedAt
            ? readPreviewStat(api, target, options)
            : undefined;
        const bodyCache = bodyCacheFor(target, updatedAt);
        return {
          metadata,
          bodyCache,
        };
      },
    };
  }, [client, uri, wikiSave, cache, cacheableIdentity]);
}
