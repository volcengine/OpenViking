import { describe, expect, it } from 'vitest'

import {
  buildSubmittedSearch,
  createRetrievalSubmission,
  parseLevels,
  parseRecallQuotas,
  validateRetrievalSearch,
} from './search-state'
import { memoryTypeFromUri } from './results'
import { DEFAULT_RECALL_QUOTAS } from '../-constants/retrieval'
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

  it('round-trips non-default recall settings', () => {
    const search = buildSubmittedSearch({
      count: 10,
      ignoreCase: false,
      includeProvenance: false,
      levels: [],
      mode: 'recall',
      path: 'resources/',
      q: 'OpenViking',
      recallMaxChars: 4000,
      recallPeerScope: 'actor',
      recallQuotas: { ...DEFAULT_RECALL_QUOTAS, experiences: 2 },
      recallRender: false,
      scoreThreshold: 0.2,
      scope: 'all',
      session: '',
      since: '',
      tags: [],
      timeField: 'updated_at',
      types: [],
      until: '',
    })

    expect(search).toMatchObject({
      maxChars: 4000,
      minScore: 0.2,
      mode: 'recall',
      peerScope: 'actor',
      q: 'OpenViking',
      render: false,
    })
    expect(parseRecallQuotas(search.recallQuotas).experiences).toBe(2)
  })

  it('builds a new submission for the selected mode', () => {
    const options: RetrievalRequestOptions = {
      contextTypes: [],
      customPathInput: 'resources/',
      ignoreCase: false,
      includeProvenance: false,
      levels: [],
      recallMaxChars: 6500,
      recallMinScore: 0.1,
      recallPeerScope: 'all',
      recallQuotas: { ...DEFAULT_RECALL_QUOTAS },
      recallRender: true,
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
    expect(createRetrievalSubmission('  ', 'recall', options)).toBeUndefined()
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
