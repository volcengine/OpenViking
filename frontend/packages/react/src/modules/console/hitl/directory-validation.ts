import { useEffect, useRef } from 'react';
import { isOvClientError } from '../../../client/errors.js';
import { OV_PATHS } from '../../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../../client/types.js';
import type { VikingBotMessage } from '../../../components/viking-bot/model.js';
import type { VikingBotInteractionFieldContext } from '../../../components/viking-bot/interaction/field-types.js';
import type { HitlCard } from './types.js';
import { object } from './json.js';
import { parseCompileUri } from './uris.js';

async function stat(client: OvClient, uri: string, options: OvRequestOptions) {
  const result = object(
    await client.request<unknown>({
      method: 'GET',
      path: OV_PATHS.fsStat,
      query: { uri },
      options,
    }),
  );
  const isDirectory = result?.isDir ?? result?.is_dir;
  if (typeof isDirectory !== 'boolean') return;
  const canonical =
    typeof result?.uri === 'string' ? parseCompileUri(result.uri) : undefined;
  return {
    uri: canonical ?? uri,
    isDirectory,
    access:
      typeof result?.access === 'string' ? result.access.toLowerCase() : '',
  };
}

type DestinationError =
  'targetReadOnly' | 'targetNotDirectory' | 'targetCheckFailed';

export async function checkDestination(
  client: OvClient,
  rawUri: string,
  options: OvRequestOptions | undefined,
  validationSignal: AbortSignal,
): Promise<{ uri: string } | { error: DestinationError } | undefined> {
  const signal = options?.signal
    ? AbortSignal.any([validationSignal, options.signal])
    : validationSignal;
  const candidate = parseCompileUri(rawUri);
  if (!candidate) return { error: 'targetCheckFailed' };
  let uri = candidate;
  while (true) {
    if (/^viking:\/\/system(?:\/|$)/.test(uri))
      return { error: 'targetReadOnly' };
    try {
      const result = await stat(client, uri, { ...options, signal });
      if (!result) return { error: 'targetCheckFailed' };
      if (!result.isDirectory) return { error: 'targetNotDirectory' };
      if (['denied', 'read', 'read-only', 'container'].includes(result.access))
        return { error: 'targetReadOnly' };
      return { uri: result.uri + candidate.slice(uri.length) };
    } catch (error) {
      if (signal.aborted) return;
      if (isOvClientError(error)) {
        if (
          error.statusCode === 401 ||
          error.statusCode === 403 ||
          error.code === 'PERMISSION_DENIED'
        )
          return { error: 'targetReadOnly' };
        const missing = error.statusCode === 404 || error.code === 'NOT_FOUND';
        const parent = uri.slice(0, uri.lastIndexOf('/'));
        if (
          missing &&
          parent.startsWith('viking://') &&
          parent.length > 'viking://'.length
        ) {
          uri = parent;
          continue;
        }
      }
      return { error: 'targetCheckFailed' };
    }
  }
}

export function sourceSuggestions(card: HitlCard, user?: VikingBotMessage) {
  if (card.cardType !== 'compile_source') return [];

  const references = [
    ...new Set(
      user?.parts.flatMap((part) =>
        part.type === 'context' && part.contextType === 'resource'
          ? [parseCompileUri(part.uri)].filter((uri) => uri !== undefined)
          : [],
      ),
    ),
  ];
  const supplied = card.payload.suggested_values;
  if (!Array.isArray(supplied)) return references;
  const allowed = new Set(
    supplied
      .filter((uri): uri is string => typeof uri === 'string')
      .map(parseCompileUri),
  );
  return references.filter((uri) => allowed.has(uri));
}

export function useSuggestedSources(
  field: VikingBotInteractionFieldContext<string>,
  suggested: readonly string[],
  client?: OvClient,
  options?: OvRequestOptions,
) {
  const edited = useRef(false);
  const current = useRef(field);
  current.current = field;
  const candidates = useRef(suggested);
  candidates.current = suggested;
  const uris = JSON.stringify(suggested);
  const { baseURL, apiKey, accountId, resourceId, userId, signal } =
    options ?? {};
  // Effect necessity: confirm suggested directories with the scoped filesystem.
  // Cancel its reads when the clarification, scope, or suggestions change.
  useEffect(() => {
    const values = candidates.current;
    if (!client || !values.length || edited.current) return;
    const request = new AbortController();
    const combined = signal
      ? AbortSignal.any([request.signal, signal])
      : request.signal;
    const requestOptions = {
      baseURL,
      apiKey,
      accountId,
      resourceId,
      userId,
      signal: combined,
    };
    void Promise.allSettled(
      values.map((uri) => stat(client, uri, requestOptions)),
    ).then((results) => {
      const draft = current.current;
      if (combined.aborted || edited.current || draft.value || draft.disabled)
        return;
      const paths = results.flatMap((result) =>
        result.status === 'fulfilled' &&
        result.value?.isDirectory &&
        !['denied', 'container'].includes(result.value.access)
          ? [result.value.uri]
          : [],
      );
      if (paths.length) draft.onChange([...new Set(paths)].join('\n'));
    });
    return () => request.abort();
  }, [uris, client, baseURL, apiKey, accountId, resourceId, userId, signal]);
  return () => {
    edited.current = true;
  };
}
