import i18n from '#/i18n/core'
import {
  getOvResult,
  isOvClientError,
  ovClient,
} from '#/shared/api/ov-client/index'

import type { SourceTrajectoryLink } from '#/features/workbench/agent-experience/lib/experience'
import {
  DEFAULT_TRAJECTORY_PAGE_SIZE,
  normalizeExperienceFiles,
  normalizeOutcomeDistribution,
  normalizeSourceTrajectoryLinks,
  normalizeTrajectoryPage,
} from '#/features/workbench/agent-experience/lib/experience'
import type {
  ExperiencePage,
  OutcomeDistribution,
  TimeRange,
  TrajectoryPage,
} from '#/features/workbench/agent-experience/lib/types'

/** Fetch one page; the extra raw entry determines whether another page exists. */
export async function fetchExperiences(options: {
  experiencesUri: string
  page: number
  pageSize: number
  signal?: AbortSignal
}): Promise<ExperiencePage> {
  const { experiencesUri, page, pageSize, signal } = options
  try {
    const result = await getOvResult<unknown>(
      ovClient.client.get({
        query: {
          limit: pageSize + 1,
          offset: (page - 1) * pageSize,
          output: 'original',
          sort_by: 'mtime',
          sort_order: 'desc',
          uri: experiencesUri,
        },
        signal,
        url: '/api/v1/fs/ls',
      }),
    )
    if (!Array.isArray(result))
      throw new Error(i18n.t('studio:errors.invalidDirectoryResponse'))
    return {
      items: normalizeExperienceFiles(result.slice(0, pageSize)),
      hasMore: result.length > pageSize,
      page,
      pageSize,
    }
  } catch (error) {
    if (isOvClientError(error) && error.statusCode === 404) {
      return { items: [], hasMore: false, page, pageSize }
    }
    throw error
  }
}

/** Search indexed experience files in this user's experience directory. */
export async function searchExperiences(options: {
  experiencesUri: string
  keyword: string
  signal?: AbortSignal
}): Promise<ExperiencePage> {
  const { experiencesUri, keyword, signal } = options
  const result = await getOvResult<unknown>(
    ovClient.client.post({
      body: {
        query: keyword,
        target_uri: experiencesUri,
        context_type: 'memory',
        level: 2,
        limit: 100,
      },
      signal,
      url: '/api/v1/search/find',
    }),
  )
  const memories =
    result && typeof result === 'object' && 'memories' in result
      ? (result as { memories?: unknown }).memories
      : undefined
  const prefix = `${experiencesUri.replace(/\/$/, '')}/`
  const items = Array.isArray(memories)
    ? memories.flatMap((match) => {
        if (
          !match ||
          typeof match !== 'object' ||
          typeof match.uri !== 'string'
        )
          return []
        const uri = match.uri as string
        const name = uri.startsWith(prefix) ? uri.slice(prefix.length) : ''
        return name && !name.includes('/') && name.endsWith('.md')
          ? [{ name, uri }]
          : []
      })
    : []
  return { items, hasMore: false, page: 1, pageSize: items.length }
}

export async function fetchContent(
  uri: string,
  signal?: AbortSignal,
): Promise<string> {
  const result = await getOvResult<unknown>(
    ovClient.client.get({
      query: { limit: -1, offset: 0, uri },
      signal,
      url: '/api/v1/content/read',
    }),
  )

  if (typeof result === 'string') return result
  if (result && typeof result === 'object') {
    const record = result as Record<string, unknown>
    if (typeof record.content === 'string') return record.content
  }
  return ''
}

export async function fetchTrajectories(options: {
  experienceUri: string
  limit?: number
  offset?: number
  timeRange?: TimeRange
  signal?: AbortSignal
}): Promise<TrajectoryPage> {
  const {
    experienceUri,
    limit = DEFAULT_TRAJECTORY_PAGE_SIZE,
    offset = 0,
    timeRange,
    signal,
  } = options
  const result = await getOvResult<unknown>(
    ovClient.client.get({
      query: {
        experience_uri: experienceUri,
        limit,
        offset,
        start_date: timeRange?.startDate,
        end_date: timeRange?.endDate,
      },
      signal,
      url: '/api/v1/agent-evolution/experiences/trajectories',
    }),
  )
  return (
    normalizeTrajectoryPage(result, experienceUri) ?? {
      experienceUri,
      items: [],
      total: 0,
      limit,
      offset,
      hasMore: false,
    }
  )
}

export async function fetchOutcomeDistribution(options: {
  experienceUri: string
  timeRange?: TimeRange
  signal?: AbortSignal
}): Promise<OutcomeDistribution> {
  const { experienceUri, timeRange, signal } = options
  const result = await getOvResult<unknown>(
    ovClient.client.get({
      query: {
        experience_uri: experienceUri,
        start_date: timeRange?.startDate,
        end_date: timeRange?.endDate,
      },
      signal,
      url: '/api/v1/agent-evolution/experiences/outcomes',
    }),
  )
  return normalizeOutcomeDistribution(result, experienceUri)
}

export async function fetchSourceTrajectories(
  uri: string,
  signal?: AbortSignal,
): Promise<SourceTrajectoryLink[]> {
  const result = await getOvResult<unknown>(
    ovClient.client.get({
      query: { uri },
      signal,
      url: '/api/v1/fs/attrs',
    }),
  )
  return normalizeSourceTrajectoryLinks(result)
}

export const fetchTrajectoryContent = fetchContent
