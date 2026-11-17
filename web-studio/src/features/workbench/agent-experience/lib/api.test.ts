import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fetchAgentEvolutionStatus,
  setAgentEvolutionEnabled,
} from '#/features/admin/account-configuration/experience-api'
import {
  fetchExperiences,
  fetchOutcomeDistribution,
  fetchTrajectories,
  searchExperiences,
} from '#/features/workbench/agent-experience/lib/api'

const { get, patch, post } = vi.hoisted(() => ({
  get: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
}))
vi.mock('#/shared/api/ov-client/index', async () => {
  const { getOvResult, isOvClientError } =
    await import('#/shared/api/ov-client/errors')
  return {
    ovClient: { client: { get, patch, post } },
    getOvResult,
    isOvClientError,
  }
})

const response = (result: unknown, hasMore?: unknown) => ({
  status: 200,
  headers: {},
  data: {
    status: 'ok',
    result,
    ...(hasMore === undefined ? {} : { has_more: hasMore }),
  },
})
const experiencesUri = 'viking://user/default/memories/experiences'
const file = (name: string) => ({ name, uri: `${experiencesUri}/${name}` })

beforeEach(() => {
  get.mockReset()
  patch.mockReset()
  post.mockReset()
})

describe('experience listing server pagination', () => {
  it('requests a page beyond the old 1000 limit and preserves has_more', async () => {
    get.mockResolvedValue(response([file('a.md'), file('b.md')], true))
    const signal = new AbortController().signal
    const result = await fetchExperiences({
      experiencesUri,
      page: 502,
      pageSize: 2,
      signal,
    })
    expect(get).toHaveBeenCalledWith({
      url: '/api/v1/fs/ls',
      signal,
      query: {
        offset: 1002,
        limit: 2,
        uri: experiencesUri,
        output: 'original',
        sort_by: 'mtime',
        sort_order: 'desc',
      },
    })
    expect(result.items.map((item) => item.name)).toEqual(['a.md', 'b.md'])
    expect(result.hasMore).toBe(true)
    expect(result).not.toHaveProperty('total')
  })

  it('keeps full final pages and advances by raw page size', async () => {
    get.mockResolvedValueOnce(response([file('a.md'), file('b.md')], true))
    get.mockResolvedValueOnce(response([file('c.md'), file('d.md')], false))
    await fetchExperiences({ experiencesUri, page: 1, pageSize: 2 })
    const last = await fetchExperiences({
      experiencesUri,
      page: 2,
      pageSize: 2,
    })
    expect(get.mock.calls[1][0].query.offset).toBe(2)
    expect(last.items.map((item) => item.name)).toEqual(['c.md', 'd.md'])
    expect(last.hasMore).toBe(false)
  })

  it('retains server pagination when a raw page contains no visible experiences', async () => {
    get.mockResolvedValue(
      response([{ ...file('folder'), isDir: true }, file('notes.json')], true),
    )
    expect(
      await fetchExperiences({ experiencesUri, page: 1, pageSize: 2 }),
    ).toEqual({
      items: [],
      hasMore: true,
      page: 1,
      pageSize: 2,
    })
  })

  it('filters metadata, nested files and files outside the requested directory', async () => {
    get.mockResolvedValue(
      response(
        [
          file('a.md'),
          file('B.MD'),
          file('.abstract.md'),
          file('nested/c.md'),
          {
            name: 'other.md',
            uri: 'viking://user/other/memories/experiences/other.md',
          },
        ],
        false,
      ),
    )
    const result = await fetchExperiences({
      experiencesUri,
      page: 1,
      pageSize: 20,
    })
    expect(result.items.map((item) => item.name)).toEqual(['a.md', 'B.MD'])
  })

  it('allows an empty later page without inventing a total', async () => {
    get.mockResolvedValue(response([], false))
    expect(
      await fetchExperiences({ experiencesUri, page: 3, pageSize: 50 }),
    ).toEqual({
      items: [],
      hasMore: false,
      page: 3,
      pageSize: 50,
    })
  })

  it.each([response([]), response({}, false), response([], 'false')])(
    'rejects invalid listing envelopes: %j',
    async (payload) => {
      get.mockResolvedValue(payload)
      await expect(
        fetchExperiences({ experiencesUri, page: 1, pageSize: 20 }),
      ).rejects.toThrow()
    },
  )

  it('treats a missing experience directory as empty', async () => {
    get.mockResolvedValue({
      status: 404,
      headers: {},
      data: {
        status: 'error',
        error: { code: 'NOT_FOUND', message: 'Missing directory' },
      },
    })
    expect(
      (await fetchExperiences({ experiencesUri, page: 1, pageSize: 20 })).items,
    ).toEqual([])
  })

  it('preserves permission failures instead of treating them as empty', async () => {
    get.mockResolvedValue({
      status: 403,
      headers: {},
      data: {
        status: 'error',
        error: { code: 'FORBIDDEN', message: 'Permission denied' },
      },
    })
    await expect(
      fetchExperiences({ experiencesUri, page: 1, pageSize: 20 }),
    ).rejects.toMatchObject({ statusCode: 403 })
  })
})

describe('native experience detail contracts', () => {
  const experienceUri = `${experiencesUri}/a.md`
  it('sends native pagination, UTC dates and cancellation for trajectories', async () => {
    get.mockResolvedValue(
      response({
        experience_uri: experienceUri,
        items: [],
        total: 0,
        limit: 20,
        offset: 20,
        has_more: false,
      }),
    )
    const signal = new AbortController().signal
    const result = await fetchTrajectories({
      experienceUri,
      offset: 20,
      timeRange: { startDate: '2026-11-01', endDate: '2026-11-18' },
      signal,
    })
    expect(get).toHaveBeenCalledWith({
      url: '/api/v1/agent-evolution/experiences/trajectories',
      signal,
      query: {
        experience_uri: experienceUri,
        limit: 20,
        offset: 20,
        start_date: '2026-11-01',
        end_date: '2026-11-18',
      },
    })
    expect(result).toMatchObject({ items: [], total: 0, hasMore: false })
  })

  it('sends native outcome parameters and preserves valid empty results', async () => {
    get.mockResolvedValue(response({ outcome_distribution: [] }))
    const signal = new AbortController().signal
    const result = await fetchOutcomeDistribution({ experienceUri, signal })
    expect(get).toHaveBeenCalledWith({
      url: '/api/v1/agent-evolution/experiences/outcomes',
      signal,
      query: {
        experience_uri: experienceUri,
        start_date: undefined,
        end_date: undefined,
      },
    })
    expect(result.distribution.every(({ count }) => count === 0)).toBe(true)
  })

  it.each([fetchTrajectories, fetchOutcomeDistribution])(
    'rejects malformed success responses',
    async (load) => {
      get.mockResolvedValue(response({}))
      await expect(load({ experienceUri })).rejects.toThrow()
    },
  )

  it.each([fetchTrajectories, fetchOutcomeDistribution])(
    'preserves unsupported-endpoint errors',
    async (load) => {
      get.mockResolvedValue({
        status: 404,
        headers: {},
        data: {
          status: 'error',
          error: { code: 'NOT_FOUND', message: 'Endpoint unavailable' },
        },
      })
      await expect(load({ experienceUri })).rejects.toMatchObject({
        statusCode: 404,
      })
    },
  )
})

describe('experience search', () => {
  it('searches direct files in the experience directory', async () => {
    post.mockResolvedValue(
      response({
        memories: [
          { uri: `${experiencesUri}/matching.md` },
          { uri: `${experiencesUri}/nested/other.md` },
          { uri: 'viking://user/other/memories/experiences/leak.md' },
        ],
      }),
    )
    const signal = new AbortController().signal
    const result = await searchExperiences({
      experiencesUri,
      keyword: 'matching',
      signal,
    })
    expect(post).toHaveBeenCalledWith({
      url: '/api/v1/search/find',
      body: {
        query: 'matching',
        target_uri: experiencesUri,
        context_type: 'memory',
        level: 2,
        limit: 100,
      },
      signal,
    })
    expect(result).toEqual({
      items: [file('matching.md')],
      hasMore: false,
      page: 1,
      pageSize: 1,
    })
  })
})

describe('account-scoped evolution settings', () => {
  it('reads the selected account effective value instead of its override', async () => {
    get.mockResolvedValue(
      response({
        account_id: 'acme',
        settings: { agent_evolution: { enabled: true } },
      }),
    )
    const signal = new AbortController().signal
    expect(await fetchAgentEvolutionStatus('acme', signal)).toEqual({
      accountId: 'acme',
      enabled: true,
    })
    expect(get).toHaveBeenCalledWith({
      url: '/api/v1/admin/accounts/acme/configuration',
      signal,
    })
    await fetchAgentEvolutionStatus('other')
    expect(get).toHaveBeenLastCalledWith({
      url: '/api/v1/admin/accounts/other/configuration',
      signal: undefined,
    })
  })

  it('patches only evolution for the selected account and reads the effective response', async () => {
    patch.mockResolvedValue(
      response({
        account_id: 'acme',
        settings: {
          agent_evolution: { enabled: false },
          acl: { enabled: true },
        },
      }),
    )
    expect(await setAgentEvolutionEnabled('acme', false)).toEqual({
      accountId: 'acme',
      enabled: false,
    })
    expect(patch).toHaveBeenCalledWith({
      url: '/api/v1/admin/accounts/acme/configuration',
      body: { settings: { agent_evolution: { enabled: false } } },
    })
  })

  it('preserves the returned account so the UI can reject a scope mismatch', async () => {
    get.mockResolvedValue(
      response({
        account_id: 'default',
        settings: { agent_evolution: { enabled: true } },
      }),
    )
    expect((await fetchAgentEvolutionStatus('acme')).accountId).toBe('default')
    get.mockResolvedValue(
      response({ settings: { agent_evolution: { enabled: true } } }),
    )
    expect((await fetchAgentEvolutionStatus('acme')).accountId).toBeUndefined()
  })
})
