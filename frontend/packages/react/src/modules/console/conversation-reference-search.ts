import { isOvClientError } from '../../client/errors.js';
import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import {
  compileSkillQueries,
  mentionPattern,
  MENTION_ROOT,
} from './mention-search.js';

export async function searchConversationReferences(
  client: OvClient,
  key: string,
  searchSkills: boolean,
  options: OvRequestOptions,
): Promise<unknown[]> {
  const queries = searchSkills
    ? compileSkillQueries(key)
    : [{ uri: MENTION_ROOT, pattern: mentionPattern(key), node_limit: 100 }];
  const results = await Promise.all(
    queries.map(async (body) => {
      try {
        const { matches } = await client.request<{ matches: unknown }>({
          method: 'POST',
          path: OV_PATHS.searchGlob,
          body,
          options,
        });
        if (!Array.isArray(matches))
          throw new Error('Invalid file search response');
        const entries: unknown[] = matches;
        return entries;
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
  return results.flat();
}
