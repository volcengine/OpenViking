import { OV_PATHS } from '../../client/paths.js';
import { isRecord } from '../../client/response.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { resolveDashboardUri } from './dashboard-resource.js';
import { normalizeResourceUri } from '../../shared/resource-uri.js';

const CONFLICT_LIMIT = 100;
const HIT_GROUPS = ['resources', 'memories', 'skills'] as const;

export class InvalidWikiConflictSearchResponse extends TypeError {}

export interface WikiConflictSummary {
  files: string[];
  directDirectories: string[];
  directFiles: string[];
  conflictFileCount: number;
  limited: boolean;
}

function readHits(result: unknown) {
  if (
    !isRecord(result) ||
    (!HIT_GROUPS.some((group) => group in result) && result.total !== 0)
  ) {
    throw new InvalidWikiConflictSearchResponse(
      'Invalid Wiki conflict search response',
    );
  }
  const hits = HIT_GROUPS.flatMap((group) => {
    const hits = result[group] ?? [];
    if (!Array.isArray(hits)) {
      throw new InvalidWikiConflictSearchResponse(
        'Invalid Wiki conflict search response',
      );
    }
    return hits as unknown[];
  });
  const { total } = result;
  if (
    total !== undefined &&
    (typeof total !== 'number' || !Number.isInteger(total) || total < 0)
  ) {
    throw new InvalidWikiConflictSearchResponse(
      'Invalid Wiki conflict search total',
    );
  }
  return { hits, total };
}

/** The data plane returns file hits. Group by the first segment for tree badges. */
export async function readWikiConflictSummary(
  client: OvClient,
  directory: string,
  options: OvRequestOptions,
): Promise<WikiConflictSummary> {
  const uri = normalizeResourceUri(
    await resolveDashboardUri(client, directory, options),
  );
  const { result, hasMore } = await client.requestEnvelope<unknown>({
    method: 'POST',
    path: OV_PATHS.searchFind,
    body: {
      query: '',
      target_uri: uri,
      tags: ['has_conflict=true'],
      level: 2,
      limit: CONFLICT_LIMIT,
    },
    options,
  });
  const { hits, total } = readHits(result);
  const prefix = uri.endsWith('/') ? uri : `${uri}/`;
  const files = new Set<string>();
  const directDirectories = new Set<string>();
  const directFiles = new Set<string>();
  for (const hit of hits) {
    if (
      !isRecord(hit) ||
      typeof hit.uri !== 'string' ||
      typeof hit.level !== 'number'
    ) {
      throw new InvalidWikiConflictSearchResponse(
        'Invalid Wiki conflict search hit',
      );
    }
    if (hit.level !== 2 || !hit.uri.startsWith(prefix)) continue;
    const relative = hit.uri.slice(prefix.length);
    if (!relative || relative.startsWith('/')) continue;
    files.add(hit.uri);
    const separator = relative.indexOf('/');
    if (separator === -1) directFiles.add(hit.uri);
    else directDirectories.add(prefix + relative.slice(0, separator));
    if (files.size === CONFLICT_LIMIT) break;
  }
  return {
    files: [...files].sort(),
    directDirectories: [...directDirectories].sort(),
    directFiles: [...directFiles].sort(),
    conflictFileCount: files.size,
    limited:
      hasMore || hits.length >= CONFLICT_LIMIT || (total ?? 0) > hits.length,
  };
}
