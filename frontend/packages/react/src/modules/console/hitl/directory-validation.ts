import { useEffect, useRef } from 'react';
import { isOvClientError } from '../../../client/errors.js';
import { OV_PATHS } from '../../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../../client/types.js';
import type { VikingBotMessage } from '../../../components/viking-bot/model.js';
import type { VikingBotInteractionFieldContext } from '../../../components/viking-bot/interaction/field-types.js';
import type { HitlCard } from './types.js';

interface DirectoryStat {
  uri?: string;
  isDir?: boolean;
  is_dir?: boolean;
  access?: string;
}

function stat(client: OvClient, uri: string, options: OvRequestOptions) {
  return client.request<DirectoryStat>({
    method: 'GET',
    path: OV_PATHS.fsStat,
    query: { uri },
    options,
  });
}

function isDirectory(result: DirectoryStat) {
  return result?.isDir === true || result?.is_dir === true;
}

export async function checkDestination(
  client: OvClient,
  rawUri: string,
  options: OvRequestOptions | undefined,
  validationSignal: AbortSignal,
) {
  const signal = options?.signal
    ? AbortSignal.any([validationSignal, options.signal])
    : validationSignal;
  let uri = rawUri.trim().replace(/\/$/, '');
  while (true) {
    if (/^viking:\/\/system(?:\/|$)/.test(uri)) return 'targetReadOnly';
    try {
      const result = await stat(client, uri, { ...options, signal });
      if (!isDirectory(result)) return 'targetNotDirectory';
      if (
        ['denied', 'read', 'read-only', 'container'].includes(
          result.access ?? '',
        )
      )
        return 'targetReadOnly';
      return;
    } catch (error) {
      if (signal.aborted) return;
      if (isOvClientError(error)) {
        if (
          error.statusCode === 401 ||
          error.statusCode === 403 ||
          error.code === 'PERMISSION_DENIED'
        )
          return 'targetReadOnly';
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
      return 'targetCheckFailed';
    }
  }
}

export function sourceSuggestions(card: HitlCard, user?: VikingBotMessage) {
  if (card.cardType !== 'compile_source') return [];
  const trim = (uri: string) => uri.replace(/\/$/, '');
  const references = [
    ...new Set(
      user?.parts.flatMap((part) =>
        part.type === 'context' && part.contextType === 'resource'
          ? [trim(part.uri)]
          : [],
      ),
    ),
  ];
  const supplied = card.payload.suggested_values;
  if (!Array.isArray(supplied)) return references;
  const allowed = new Set(
    supplied.filter((uri): uri is string => typeof uri === 'string').map(trim),
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
      const paths = results.flatMap((result, index) =>
        result.status === 'fulfilled' &&
        isDirectory(result.value) &&
        !['denied', 'container'].includes(result.value.access ?? '')
          ? [result.value.uri || values[index]!]
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
