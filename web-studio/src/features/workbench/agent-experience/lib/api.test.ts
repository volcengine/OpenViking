import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fetchAgentEvolutionStatus,
  setAgentEvolutionEnabled,
} from '#/features/admin/account-configuration/experience-api'
import {
  fetchExperiences,
  searchExperiences,
} from '#/features/workbench/agent-experience/lib/api'

const { get, patch, post } = vi.hoisted(() => ({
  get: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
}))
vi.mock('#/shared/api/ov-client/index', () => ({
  ovClient: { client: { get, patch, post } },
  getOvResult: (result: unknown) => result,
  isOvClientError: () => false,
}))

const experiencesUri = 'viking://user/default/memories/experiences'
const file = (name: string) => ({ name, uri: `${experiencesUri}/${name}` })
const directory = (name: string) => ({ ...file(name), isDir: true })

describe('experience listing server pagination', () => {
  beforeEach(() => {
    get.mockReset()
  })

  it('requests a page beyond the old 1000 limit with one lookahead entry', async () => {
    get.mockResolvedValue([file('a.md'), file('b.md'), file('c.md')])
    const result = await fetchExperiences({
      experiencesUri,
      offset: 1002,
      pageSize: 2,
    })
    expect(get).toHaveBeenCalledWith(
      expect.objectContaining({
        url: '/api/v1/fs/ls',
        query: expect.objectContaining({
          offset: 1002,
          limit: 3,
          uri: experiencesUri,
        }),
      }),
    )
    expect(result.items.map((item) => item.name)).toEqual(['a.md', 'b.md'])
    expect(result.nextOffset).toBe(1004)
    expect(result).not.toHaveProperty('total')
  })

  it('does not skip lookahead records between pages', async () => {
    get.mockResolvedValueOnce([file('a.md'), file('b.md'), file('c.md')])
    get.mockResolvedValueOnce([file('c.md'), file('d.md')])
    const first = await fetchExperiences({
      experiencesUri,
      offset: 0,
      pageSize: 2,
    })
    expect(first.nextOffset).toBe(2)
    const last = await fetchExperiences({
      experiencesUri,
      offset: first.nextOffset!,
      pageSize: 2,
    })
    expect(get.mock.calls[1][0].query.offset).toBe(2)
    expect(last.items.map((item) => item.name)).toEqual(['c.md', 'd.md'])
    expect(last.nextOffset).toBeUndefined()
  })

  it('fills an initially empty page and resumes at the next undisplayed file', async () => {
    const listing = [
      ...Array.from({ length: 41 }, (_, i) => directory(`legacy-${i}.md`)),
      ...Array.from({ length: 11 }, (_, i) => file(`experience-${i}.md`)),
    ]
    get.mockImplementation(({ query }) =>
      Promise.resolve(listing.slice(query.offset, query.offset + query.limit)),
    )

    const first = await fetchExperiences({
      experiencesUri,
      offset: 0,
      pageSize: 10,
    })
    expect(first.items.map((item) => item.name)).toEqual(
      Array.from({ length: 10 }, (_, i) => `experience-${i}.md`),
    )
    expect(first.nextOffset).toBe(51)

    const last = await fetchExperiences({
      experiencesUri,
      offset: first.nextOffset!,
      pageSize: 10,
    })
    expect(last.items.map((item) => item.name)).toEqual(['experience-10.md'])
    expect(last.nextOffset).toBeUndefined()
    expect(get.mock.calls.map(([request]) => request.query.offset)).toEqual([
      0, 11, 22, 33, 44, 51,
    ])
  })

  it('ends a full page when the remaining entries contain no displayable files', async () => {
    get.mockResolvedValueOnce([file('a.md'), file('b.md'), directory('folder')])
    get.mockResolvedValueOnce([{ name: 'missing-uri.md' }])

    const result = await fetchExperiences({
      experiencesUri,
      offset: 0,
      pageSize: 2,
    })
    expect(result.items.map((item) => item.name)).toEqual(['a.md', 'b.md'])
    expect(result.nextOffset).toBeUndefined()
    expect(get).toHaveBeenCalledTimes(2)
  })

  it('allows an empty later page without inventing a total', async () => {
    get.mockResolvedValue([])
    const result = await fetchExperiences({
      experiencesUri,
      offset: 100,
      pageSize: 50,
    })
    expect(result).toEqual({ items: [] })
  })
})

describe('experience search', () => {
  beforeEach(() => post.mockReset())

  it('searches the experience directory with find and returns direct files without pagination', async () => {
    post.mockResolvedValue({
      memories: [
        { uri: `${experiencesUri}/matching.md` },
        { uri: `${experiencesUri}/nested/other.md` },
        { uri: 'viking://user/other/memories/experiences/leak.md' },
      ],
    })
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
    })
  })
})

describe('account-scoped evolution settings', () => {
  beforeEach(() => {
    get.mockReset()
    patch.mockReset()
  })

  it('reads the selected account effective value instead of its override', async () => {
    get.mockResolvedValue({
      account_id: 'acme',
      settings: { agent_evolution: { enabled: true } },
    })
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
    patch.mockResolvedValue({
      account_id: 'acme',
      settings: { agent_evolution: { enabled: false }, acl: { enabled: true } },
    })
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
    get.mockResolvedValue({
      account_id: 'default',
      settings: { agent_evolution: { enabled: true } },
    })
    expect((await fetchAgentEvolutionStatus('acme')).accountId).toBe('default')
    get.mockResolvedValue({ settings: { agent_evolution: { enabled: true } } })
    expect((await fetchAgentEvolutionStatus('acme')).accountId).toBeUndefined()
  })
})
