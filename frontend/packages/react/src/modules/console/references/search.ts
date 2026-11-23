import { isOvClientError } from '../../../client/errors.js';
import { OV_PATHS } from '../../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../../client/types.js';
import {
  compileSkillQueries,
  mentionPattern,
  MENTION_ROOT,
} from '../mention-search.js';

export function referenceSearchInput(query: string, searchSkills: boolean) {
  const trimmed = query.trim();
  const keyword = trimmed
    .replace(/^viking:\/\//i, '')
    .split('/')
    .filter(Boolean)
    .join('/');
  return {
    keyword,
    searchKey:
      keyword && searchSkills && /^viking:\/\//i.test(trimmed)
        ? `${MENTION_ROOT}${keyword}`
        : keyword,
  };
}

/** Search each reference root while preserving response validation and missing Skill roots. */
export function searchReferenceMatches(
  { client, options }: { client: OvClient; options: OvRequestOptions },
  searchKey: string,
  signal: AbortSignal,
  searchSkills: boolean,
): Promise<unknown[][]> {
  const queries = searchSkills
    ? compileSkillQueries(searchKey)
    : [
        {
          uri: MENTION_ROOT,
          pattern: mentionPattern(searchKey),
          node_limit: 100,
        },
      ];
  return Promise.all(
    queries.map(async (body): Promise<unknown[]> => {
      try {
        const { matches: rawMatches } = await client.request<{
          matches: unknown;
        }>({
          method: 'POST',
          path: OV_PATHS.searchGlob,
          body,
          options: { ...options, signal },
        });
        if (!Array.isArray(rawMatches))
          throw new Error('Invalid file search response');
        const matches: unknown[] = rawMatches;
        return matches;
      } catch (error) {
        if (
          searchSkills &&
          isOvClientError(error) &&
          (error.code === 'NOT_FOUND' || error.statusCode === 404)
        )
          return [];
        throw error;
      }
    }),
  );
}
