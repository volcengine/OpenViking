import { normalizeResourceUri } from './fs-uri.js';
import { OV_PATHS } from '../../client/paths.js';
import { resolveDashboardUri } from './dashboard-resource.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type {
  ExperienceTrajectory,
  ExperienceOutcome,
  ExperienceItem,
} from '../../components/experience-dashboard/types.js';

export const EXPERIENCE_PAGE_SIZE = 20;
export const isExperienceDirectory = (uri: string) =>
  /^viking:\/\/(?:user\/[^/]+|~)(?:\/peers\/[^/]+)?\/memories\/experiences\/?$/.test(
    normalizeResourceUri(uri),
  );
export const isExperienceFile = (uri: string) =>
  /^viking:\/\/(?:user\/[^/]+|~)(?:\/peers\/[^/]+)?\/memories\/experiences\/[^/]+\.md$/i.test(
    normalizeResourceUri(uri),
  );

/** Native API date bounds are UTC and inclusive. */
export function experienceDateRange(days: number, now = new Date()) {
  if (!days) return {};
  const start = new Date(now);
  start.setUTCDate(start.getUTCDate() - days + 1);
  return {
    start_date: start.toISOString().slice(0, 10),
    end_date: now.toISOString().slice(0, 10),
  };
}

export async function readExperiences(
  client: OvClient,
  uri: string,
  page: number,
  options: OvRequestOptions,
) {
  uri = await resolveDashboardUri(client, uri, options);
  const result = await client.requestEnvelope<
    (ExperienceItem & { isDir?: boolean })[]
  >({
    method: 'GET',
    path: OV_PATHS.fsLs,
    query: {
      uri,
      output: 'original',
      limit: EXPERIENCE_PAGE_SIZE,
      offset: page * EXPERIENCE_PAGE_SIZE,
      sort_by: 'mtime',
      sort_order: 'desc',
    },
    options,
  });
  if (!Array.isArray(result.result))
    throw new TypeError('Invalid Experience listing');
  const prefix = `${uri.replace(/\/+$/, '')}/`;
  return {
    items: result.result.filter(
      (item) =>
        !item.isDir &&
        item.uri.startsWith(prefix) &&
        isExperienceFile(item.uri),
    ),
    hasMore: result.hasMore,
  };
}

export async function readExperienceSources(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
) {
  uri = await resolveDashboardUri(client, uri, options);
  const result = await client.request<{
    attrs?: {
      memory?: {
        links?: {
          to_uri: string;
          link_type: string;
          description?: string;
          match_text?: string;
        }[];
      };
    };
  }>({ method: 'GET', path: OV_PATHS.fsAttrs, query: { uri }, options });
  const links = result?.attrs?.memory?.links;
  if (links !== undefined && !Array.isArray(links))
    throw new TypeError('Invalid Experience links');
  const prefix =
    uri.slice(0, uri.indexOf('/memories/experiences/')) +
    '/memories/trajectories/';
  const seen = new Set<string>();
  return (links ?? []).flatMap((link): ExperienceTrajectory[] => {
    if (
      link.link_type !== 'derived_from' ||
      typeof link.to_uri !== 'string' ||
      !link.to_uri.startsWith(prefix) ||
      seen.has(link.to_uri)
    )
      return [];
    seen.add(link.to_uri);
    return [
      {
        uri: link.to_uri,
        name: link.to_uri.split('/').pop() ?? link.to_uri,
        description: link.description ?? link.match_text,
      },
    ];
  });
}

export interface ExperienceTrajectoryPage {
  items: ExperienceTrajectory[];
  total: number;
  has_more: boolean;
}
export interface ExperienceOutcomeResult {
  outcome_distribution: { outcome: ExperienceOutcome; count: number }[];
}
