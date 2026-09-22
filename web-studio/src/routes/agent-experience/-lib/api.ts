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
  ExperiencePage,
  OutcomeDistribution,
  TimeRange,
  TrajectoryPage,
} from './types'

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
    if (!Array.isArray(result)) throw new Error('Invalid fs/ls response')
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

type AccountSettingsResult = {
  account_id?: string
  settings?: { agent_evolution?: { enabled?: boolean } }
}

function normalizeAgentEvolutionStatus(
  result: AccountSettingsResult,
): AgentEvolutionStatus {
  return {
    enabled: result.settings?.agent_evolution?.enabled === true,
    accountId:
      typeof result.account_id === 'string' ? result.account_id : undefined,
  }
}

export async function fetchAgentEvolutionStatus(
  accountId: string,
  signal?: AbortSignal,
): Promise<AgentEvolutionStatus> {
  const result = await getOvResult<AccountSettingsResult>(
    ovClient.client.get({
      signal,
      url: `/api/v1/admin/accounts/${encodeURIComponent(accountId)}/settings`,
    }),
  )
  return normalizeAgentEvolutionStatus(result)
}

export async function setAgentEvolutionEnabled(
  accountId: string,
  enabled: boolean,
): Promise<AgentEvolutionStatus> {
  const result = await getOvResult<AccountSettingsResult>(
    ovClient.client.patch({
      body: { agent_evolution: { enabled } },
      url: `/api/v1/admin/accounts/${encodeURIComponent(accountId)}/settings`,
    }),
  )
  return normalizeAgentEvolutionStatus(result)
}

export const fetchTrajectoryContent = fetchContent
