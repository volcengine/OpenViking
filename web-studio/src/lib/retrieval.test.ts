import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchFindAllTypes, fetchGlob, fetchGrep } from './retrieval'

const { postSearchFindMock, postSearchGlobMock, postSearchGrepMock } =
  vi.hoisted(() => ({
    postSearchFindMock: vi.fn(),
    postSearchGlobMock: vi.fn(),
    postSearchGrepMock: vi.fn(),
  }))

vi.mock('#/lib/ov-client', () => ({
  getOvResult: async (request: Promise<unknown>) => {
    const rawResponse = (await request) as {
      data: { result: unknown }
    }
    return rawResponse.data.result
  },
  normalizeOvClientError: (error: unknown) => error,
  postSearchFind: postSearchFindMock,
  postSearchGlob: postSearchGlobMock,
  postSearchGrep: postSearchGrepMock,
  postSearchSearch: vi.fn(),
}))

function response(result: unknown) {
  return Promise.resolve({
    data: { result, status: 'ok' },
    headers: {},
    status: 200,
  })
}

describe('pattern retrieval', () => {
  beforeEach(() => {
    postSearchFindMock.mockReset()
    postSearchGlobMock.mockReset()
    postSearchGrepMock.mockReset()
  })

  it('uses one find request for the server-grouped result types', async () => {
    postSearchFindMock.mockReturnValue(
      response({
        memories: [{ uri: 'viking://user/default/memories/profile.md' }],
        resources: [{ uri: 'viking://resources/guide.md' }],
        skills: [{ uri: 'viking://user/default/skills/reviewer' }],
        total: 3,
      }),
    )

    const result = await fetchFindAllTypes('OpenViking', { limit: 10 })

    expect(postSearchFindMock).toHaveBeenCalledTimes(1)
    expect(postSearchFindMock).toHaveBeenCalledWith({
      body: {
        filter: undefined,
        limit: 10,
        query: 'OpenViking',
        score_threshold: undefined,
        target_uri: undefined,
      },
    })
    expect(result).toMatchObject({
      total: 3,
      memories: [{ context_type: 'memory' }],
      resources: [{ context_type: 'resource' }],
      skills: [{ context_type: 'skill' }],
    })
  })

  it('maps grep line matches into retrieval result rows', async () => {
    postSearchGrepMock.mockReturnValue(
      response({
        count: 1,
        matches: [
          {
            content: 'OpenViking authentication',
            line: 15,
            uri: 'viking://resources/docs/auth.md',
          },
        ],
      }),
    )

    const result = await fetchGrep('authentication', {
      caseInsensitive: true,
      limit: 20,
      uri: 'viking://resources/',
    })

    expect(postSearchGrepMock).toHaveBeenCalledWith({
      body: {
        case_insensitive: true,
        node_limit: 20,
        pattern: 'authentication',
        uri: 'viking://resources/',
      },
    })
    expect(result.resources[0]).toMatchObject({
      abstract: 'OpenViking authentication',
      line: 15,
      result_kind: 'grep',
      uri: 'viking://resources/docs/auth.md',
    })
    expect(result.total).toBe(1)
  })

  it('maps glob URI matches into retrieval result rows', async () => {
    postSearchGlobMock.mockReturnValue(
      response({
        count: 2,
        matches: [
          'viking://resources/docs/api.md',
          'viking://resources/docs/guide.md',
        ],
      }),
    )

    const result = await fetchGlob('**/*.md', {
      limit: 50,
      uri: 'viking://',
    })

    expect(postSearchGlobMock).toHaveBeenCalledWith({
      body: {
        node_limit: 50,
        pattern: '**/*.md',
        uri: 'viking://',
      },
    })
    expect(
      result.resources.map(({ result_kind, uri }) => [result_kind, uri]),
    ).toEqual([
      ['glob', 'viking://resources/docs/api.md'],
      ['glob', 'viking://resources/docs/guide.md'],
    ])
    expect(result.total).toBe(2)
  })
})
