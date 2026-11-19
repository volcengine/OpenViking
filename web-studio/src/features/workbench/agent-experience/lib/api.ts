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

/** Preserve the native listing envelope's pagination before normalizing entries. */
export async function fetchExperiences(options: {
  experiencesUri: string
  page: number
  pageSize: number
  signal?: AbortSignal
}): Promise<ExperiencePage> {
  const { experiencesUri, page, pageSize, signal } = options
  try {
    const request = ovClient.client.get({
      query: {
        limit: pageSize,
        offset: (page - 1) * pageSize,
        output: 'original',
        sort_by: 'mtime',
        sort_order: 'desc',
        uri: experiencesUri,
      },
      signal,
      url: '/api/v1/fs/ls',
    })
    const result = await getOvResult<unknown>(request)
    const envelope = (await request).data as { has_more?: unknown } | undefined
    if (!Array.isArray(result) || typeof envelope?.has_more !== 'boolean')
      throw new Error(i18n.t('studio:errors.invalidDirectoryResponse'))
    const prefix = `${experiencesUri.replace(/\/+$/, '')}/`
    return {
      items: normalizeExperienceFiles(result).filter(
        ({ uri }) =>
          uri.startsWith(prefix) && !uri.slice(prefix.length).includes('/'),
      ),
      hasMore: envelope.has_more,
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
        return name && !name.includes('/') && /\.md$/i.test(name)
          ? [{ name, uri }]
          : []
      })
    : []
  return { items, hasMore: false, page: 1, pageSize: items.length }
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
  const page = normalizeTrajectoryPage(result, experienceUri)
  if (!page) throw new Error(i18n.t('studio:errors.invalidExperienceResponse'))
  return page
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
  const distribution = normalizeOutcomeDistribution(result, experienceUri)
  if (!distribution)
    throw new Error(i18n.t('studio:errors.invalidExperienceResponse'))
  return distribution
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
