import { describe, expect, it } from 'vitest'

import {
  buildSubmittedSearch,
  createRetrievalSubmission,
  parseLevels,
  validateRetrievalSearch,
} from './search-state'
import { memoryTypeFromUri } from './results'
import type { RetrievalRequestOptions } from '../-types/retrieval'

describe('retrieval search state', () => {
  it('validates advanced parameters from URL search values', () => {
    expect(
      validateRetrievalSearch({
        levels: '0,2',
        minScore: '0.25',
        provenance: 'true',
        tags: 'env=prod,team=search',
        timeField: 'created_at',
        types: 'memory,resource',
      }),
    ).toMatchObject({
      levels: '0,2',
      minScore: 0.25,
      provenance: true,
      tags: 'env=prod,team=search',
      timeField: 'created_at',
      types: 'memory,resource',
    })
    expect(parseLevels('0,2,8')).toEqual([0, 2])
  })

  it('builds a new submission for the selected mode', () => {
    const options: RetrievalRequestOptions = {
      contextTypes: [],
      customPathInput: 'resources/',
      ignoreCase: false,
      includeProvenance: false,
      levels: [],
      resultCount: 10,
      scope: 'all',
      tags: [],
      timeField: 'updated_at',
    }

    expect(
      createRetrievalSubmission(' openviking ', 'search', options),
    ).toEqual({
      query: 'openviking',
      search: { mode: 'search', q: 'openviking' },
    })
    expect(createRetrievalSubmission('  ', 'find', options)).toBeUndefined()
  })

  it('drops the retired recall mode and its parameters', () => {
    expect(
      validateRetrievalSearch({
        maxChars: 6500,
        mode: 'recall',
        peerScope: 'all',
        q: 'OpenViking',
        recallQuotas: 'events:10',
        render: false,
      }),
    ).toEqual({ q: 'OpenViking' })
  })

  it('derives memory type labels without leaking file extensions', () => {
    expect(
      memoryTypeFromUri(
        'viking://user/default/memories/trajectories/.abstract.md',
      ),
    ).toBe('TRAJECTORIES')
    expect(memoryTypeFromUri('viking://user/default/memories/profile.md')).toBe(
      'PROFILE',
    )
  })
})
