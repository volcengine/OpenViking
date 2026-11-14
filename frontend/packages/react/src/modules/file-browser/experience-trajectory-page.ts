import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import {
  experienceDateQuery,
  EXPERIENCE_PAGE_SIZE,
} from './experience-data.js';
import type { ExperienceTrajectoryPage } from './experience-data.js';
import type { ExperienceTrajectory } from '../../components/experience-dashboard/types.js';

/**
 * 取一页轨迹并校验形状。
 *
 * Fetches one page of trajectories and validates its shape.
 */
export async function readExperienceTrajectoryPage(
  client: OvClient,
  uri: string,
  days: number,
  page: number,
  options: OvRequestOptions,
): Promise<ExperienceTrajectoryPage> {
  const { startDate, endDate } = experienceDateQuery(days);
  const result = await client.request<ExperienceTrajectoryPage>({
    method: 'GET',
    path: OV_PATHS.experienceTrajectories,
    query: {
      experience_uri: uri,
      offset: page * EXPERIENCE_PAGE_SIZE,
      limit: EXPERIENCE_PAGE_SIZE,
      start_date: startDate,
      end_date: endDate,
    },
    options,
  });
  if (
    !Array.isArray(result?.items) ||
    !Number.isSafeInteger(result.total) ||
    result.total < 0 ||
    result.items.some(
      (item: ExperienceTrajectory | null) =>
        typeof item?.uri !== 'string' || typeof item.name !== 'string',
    )
  )
    throw new TypeError('Invalid Experience trajectories');
  return result;
}
