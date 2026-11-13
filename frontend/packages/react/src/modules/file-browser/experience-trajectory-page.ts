import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { toApiDate } from '../../shared/primitives/date-picker/api-date.js';
import { experienceDateBounds } from './experience-date-range.js';
import { EXPERIENCE_PAGE_SIZE } from './experience-data.js';
import type { ExperienceTrajectoryPage } from './experience-data.js';

/**
 * 把天数换成接口的日期查询参数（与目录列表共用同一条 UTC 约定）。
 *
 * Turns a day count into the endpoint's date query parameters, sharing the directory
 * list's UTC rule.
 */
function dateQuery(days: number) {
  const bounds = experienceDateBounds(days);
  if (!bounds) return {};
  return {
    start_date: toApiDate(bounds.start, 'utc'),
    end_date: toApiDate(bounds.end, 'utc'),
  };
}

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
  const result = await client.request<ExperienceTrajectoryPage>({
    method: 'GET',
    path: OV_PATHS.experienceTrajectories,
    query: {
      experience_uri: uri,
      offset: page * EXPERIENCE_PAGE_SIZE,
      limit: EXPERIENCE_PAGE_SIZE,
      ...dateQuery(days),
    },
    options,
  });
  if (
    !Array.isArray(result?.items) ||
    !Number.isSafeInteger(result.total) ||
    result.total < 0 ||
    result.items.some(
      (item) => typeof item?.uri !== 'string' || typeof item.name !== 'string',
    )
  )
    throw new TypeError('Invalid Experience trajectories');
  return result;
}
