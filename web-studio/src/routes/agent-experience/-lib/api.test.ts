import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fetchAgentEvolutionStatus,
  fetchExperiences,
  setAgentEvolutionEnabled,
} from './api'

const { get, patch } = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn() }))
vi.mock('#/lib/ov-client', () => ({
  ovClient: { client: { get, patch } },
  getOvResult: (result: unknown) => result,
  isOvClientError: () => false,
}))

const experiencesUri = 'viking://user/default/memories/experiences'
const file = (name: string) => ({ name, uri: `${experiencesUri}/${name}` })
const mockListing = (entries: unknown[]) => {
  get.mockImplementation(
    (request?: { query: { offset: number; limit: number } }) => {
      const query = request?.query
      return Promise.resolve(
        query ? entries.slice(query.offset, query.offset + query.limit) : [],
      )
    },
  )
}

describe('experience listing server pagination', () => {
  beforeEach(() => get.mockReset())

  it('requests a page beyond the old 1000 limit with one lookahead entry', async () => {
    get.mockImplementation((request?: { query: { offset: number } }) =>
      Promise.resolve(
        request?.query.offset === 1002
          ? [file('a.md'), file('b.md'), file('c.md')]
          : [file('first.md')],
      ),
    )
    const result = await fetchExperiences({
      experiencesUri,
      page: 502,
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
    expect(result.hasMore).toBe(true)
    expect(result).not.toHaveProperty('total')
  })

  it('does not skip lookahead records between pages', async () => {
    mockListing([file('a.md'), file('b.md'), file('c.md'), file('d.md')])
    await fetchExperiences({ experiencesUri, page: 1, pageSize: 2 })
    const last = await fetchExperiences({
      experiencesUri,
      page: 2,
      pageSize: 2,
    })
    expect(last.items.map((item) => item.name)).toEqual(['c.md', 'd.md'])
    expect(last.hasMore).toBe(false)
  })

  it('shows file pages after a directory-only prefix', async () => {
    mockListing([
      ...Array.from({ length: 125 }, (_, index) => ({
        ...file(`folder-${index}`),
        isDir: true,
      })),
      ...['a.md', 'b.md', 'c.md', 'd.md', 'e.md'].map(file),
    ])
    const first = await fetchExperiences({
      experiencesUri,
      page: 1,
      pageSize: 2,
    })
    expect(first.items.map((item) => item.name)).toEqual(['a.md', 'b.md'])
    expect(first.hasMore).toBe(true)

    const last = await fetchExperiences({
      experiencesUri,
      page: 3,
      pageSize: 2,
    })
    expect(last.items.map((item) => item.name)).toEqual(['e.md'])
    expect(last.hasMore).toBe(false)
  })

  it('jumps to a distant file page after scanning the directory prefix', async () => {
    mockListing([
      ...Array.from({ length: 125 }, (_, index) => ({
        ...file(`folder-${index}`),
        isDir: true,
      })),
      ...Array.from({ length: 1005 }, (_, index) => file(`file-${index}.md`)),
    ])
    const result = await fetchExperiences({
      experiencesUri,
      page: 502,
      pageSize: 2,
    })
    expect(result.items.map((item) => item.name)).toEqual([
      'file-1002.md',
      'file-1003.md',
    ])
    expect(result.hasMore).toBe(true)
    expect(get.mock.calls.length).toBeLessThan(10)
  })

  it('does not offer another page when the listing contains only directories', async () => {
    mockListing(
      Array.from({ length: 5 }, (_, index) => ({
        ...file(`folder-${index}`),
        isDir: true,
      })),
    )
    const result = await fetchExperiences({
      experiencesUri,
      page: 1,
      pageSize: 2,
    })
    expect(result.items).toEqual([])
    expect(result.hasMore).toBe(false)
  })

  it('allows an empty later page without inventing a total', async () => {
    get.mockResolvedValue([])
    const result = await fetchExperiences({
      experiencesUri,
      page: 3,
      pageSize: 50,
    })
    expect(result).toEqual({ items: [], hasMore: false, page: 3, pageSize: 50 })
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
      overrides: {},
    })
    const signal = new AbortController().signal
    expect(await fetchAgentEvolutionStatus('acme', signal)).toEqual({
      accountId: 'acme',
      enabled: true,
    })
    expect(get).toHaveBeenCalledWith({
      url: '/api/v1/admin/accounts/acme/settings',
      signal,
    })
    await fetchAgentEvolutionStatus('other')
    expect(get).toHaveBeenLastCalledWith({
      url: '/api/v1/admin/accounts/other/settings',
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
      url: '/api/v1/admin/accounts/acme/settings',
      body: { agent_evolution: { enabled: false } },
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
