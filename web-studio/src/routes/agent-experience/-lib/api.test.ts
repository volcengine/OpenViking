import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchExperiences } from './api'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('#/lib/ov-client', () => ({
  ovClient: { client: { get } },
  getOvResult: (result: unknown) => result,
  isOvClientError: () => false,
}))

const experiencesUri = 'viking://user/default/memories/experiences'
const file = (name: string) => ({ name, uri: `${experiencesUri}/${name}` })

describe('experience listing server pagination', () => {
  beforeEach(() => get.mockReset())

  it('requests a page beyond the old 1000 limit with one lookahead entry', async () => {
    get.mockResolvedValue([file('a.md'), file('b.md'), file('c.md')])
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
    get.mockResolvedValueOnce([file('a.md'), file('b.md'), file('c.md')])
    get.mockResolvedValueOnce([file('c.md'), file('d.md')])
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

  it('applies pagination to raw entries before filtering out directories', async () => {
    get.mockResolvedValue([
      { ...file('folder'), isDir: true },
      file('a.md'),
      file('b.md'),
    ])
    const result = await fetchExperiences({
      experiencesUri,
      page: 1,
      pageSize: 2,
    })
    expect(result.items.map((item) => item.name)).toEqual(['a.md'])
    expect(result.hasMore).toBe(true)
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
