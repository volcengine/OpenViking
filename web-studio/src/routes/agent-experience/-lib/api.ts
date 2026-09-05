import { getOvResult, isOvClientError, ovClient } from '#/lib/ov-client'

import {
  DEFAULT_TRAJECTORY_PAGE_SIZE,
  normalizeExperienceFiles,
  normalizeOutcomeDistribution,
  normalizeSourceTrajectoryLinks,
  normalizeTrajectoryPage,
} from './experience'
import type { SourceTrajectoryLink } from './experience'
import type {
  AgentEvolutionStatus,
  ExperienceFileItem,
  ExperiencePage,
  OutcomeDistribution,
  TimeRange,
  TrajectoryPage,
} from './types'

const EXPERIENCE_LIST_LIMIT = 1000

async function fetchExperienceFiles(
  experiencesUri: string,
  signal?: AbortSignal,
): Promise<ExperienceFileItem[]> {
  try {
    const result = await getOvResult<unknown>(
      ovClient.client.get({
        query: {
          node_limit: EXPERIENCE_LIST_LIMIT,
          output: 'original',
          sort_by: 'mtime',
          sort_order: 'desc',
          uri: experiencesUri,
        },
        signal,
        url: '/api/v1/fs/ls',
      }),
    )
    return normalizeExperienceFiles(result)
  } catch (error) {
    if (isOvClientError(error) && error.statusCode === 404) return []
    throw error
  }
}

/** List and page Experience files from a self-hosted OpenViking server. */
export async function fetchExperiences(options: {
  experiencesUri: string
  keyword: string
  page: number
  pageSize: number
  signal?: AbortSignal
}): Promise<ExperiencePage> {
  const { experiencesUri, keyword, page, pageSize, signal } = options
  const allItems = await fetchExperienceFiles(experiencesUri, signal)
  const normalizedKeyword = keyword.trim().toLocaleLowerCase()
  const filteredItems = normalizedKeyword
    ? allItems.filter(
        (item) =>
          item.name.toLocaleLowerCase().includes(normalizedKeyword) ||
          item.uri.toLocaleLowerCase().includes(normalizedKeyword),
      )
    : allItems
  const offset = (page - 1) * pageSize
  return {
    items: filteredItems.slice(offset, offset + pageSize),
    total: filteredItems.length,
    page,
    pageSize,
  }
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

export async function fetchAgentEvolutionStatus(
  signal?: AbortSignal,
): Promise<AgentEvolutionStatus> {
  const result = await getOvResult<unknown>(
    ovClient.client.get({ signal, url: '/api/v1/admin/agent-evolution' }),
  )
  const record =
    result && typeof result === 'object'
      ? (result as Record<string, unknown>)
      : {}
  return {
    enabled: record.enabled === true,
    accountId:
      typeof record.account_id === 'string' ? record.account_id : undefined,
  }
}

export async function setAgentEvolutionEnabled(
  enabled: boolean,
): Promise<AgentEvolutionStatus> {
  const result = await getOvResult<unknown>(
    ovClient.client.put({
      body: { enabled },
      url: '/api/v1/admin/agent-evolution',
    }),
  )
  const record =
    result && typeof result === 'object'
      ? (result as Record<string, unknown>)
      : {}
  return {
    enabled: record.enabled === true,
    accountId:
      typeof record.account_id === 'string' ? record.account_id : undefined,
  }
}

export const fetchTrajectoryContent = fetchContent
