// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'

import {
  buildExperiencesUri,
  normalizeOutcomeDistribution,
  normalizeTrajectoryPage,
  resolveTimeRange,
} from '#/features/workbench/agent-experience/lib/experience'

describe('buildExperiencesUri', () => {
  it('builds the experiences directory URI for a user', () => {
    expect(buildExperiencesUri('default')).toBe(
      'viking://user/default/memories/experiences',
    )
    expect(buildExperiencesUri('glm-4.7')).toBe(
      'viking://user/glm-4.7/memories/experiences',
    )
  })

  it('encodes user ids with reserved characters', () => {
    expect(buildExperiencesUri('user/with/slashes')).toBe(
      'viking://user/user%2Fwith%2Fslashes/memories/experiences',
    )
  })

  it('falls back to the default user for empty ids', () => {
    expect(buildExperiencesUri('')).toBe(
      'viking://user/default/memories/experiences',
    )
    expect(buildExperiencesUri('  ')).toBe(
      'viking://user/default/memories/experiences',
    )
  })
})

describe('normalizeTrajectoryPage', () => {
  const payload = {
    experience_uri: 'viking://user/u/memories/experiences/e.md',
    items: [
      {
        uri: 'viking://user/u/memories/trajectories/t1.md',
        name: 't1.md',
        description: '处理换货请求',
        created_at: '2026-08-05T02:00:00Z',
        updated_at: '2026-08-05T02:10:00Z',
      },
      { name: 'missing-uri.md' },
    ],
    total: 21,
    limit: 20,
    offset: 0,
    has_more: true,
  }

  it('normalizes items, pagination and has_more', () => {
    const page = normalizeTrajectoryPage(payload, 'fallback')
    expect(page).not.toBeNull()
    expect(page?.items).toHaveLength(1)
    expect(page?.items[0].name).toBe('t1.md')
    expect(page?.total).toBe(21)
    expect(page?.hasMore).toBe(true)
  })

  it('derives has_more when the flag is missing', () => {
    const page = normalizeTrajectoryPage(
      { items: [{ uri: 'viking://t/1', name: 't' }], total: 2, offset: 0 },
      'fallback',
    )
    expect(page?.hasMore).toBe(true)
  })

  it('returns null for non-object payloads', () => {
    expect(normalizeTrajectoryPage('nope', 'fallback')).toBeNull()
    expect(normalizeTrajectoryPage(undefined, 'fallback')).toBeNull()
  })

  it('falls back to the provided uri when the response omits it', () => {
    const page = normalizeTrajectoryPage({ items: [] }, 'fallback-uri')
    expect(page?.experienceUri).toBe('fallback-uri')
  })
})

describe('normalizeOutcomeDistribution', () => {
  it('fills the five fixed buckets and zeroes missing counts', () => {
    const result = normalizeOutcomeDistribution(
      {
        outcome_distribution: [
          { outcome: 'success', count: 4 },
          { outcome: 'failure', count: 1 },
        ],
      },
      'viking://user/u/memories/experiences/e.md',
    )

    expect(result.distribution).toEqual([
      { outcome: 'success', count: 4 },
      { outcome: 'failure', count: 1 },
      { outcome: 'partial', count: 0 },
      { outcome: 'unknown', count: 0 },
      { outcome: 'unfinished', count: 0 },
    ])
  })

  it('keeps the stable order regardless of server ordering', () => {
    const result = normalizeOutcomeDistribution(
      {
        outcome_distribution: [
          { outcome: 'unfinished', count: 2 },
          { outcome: 'partial', count: 3 },
        ],
      },
      'uri',
    )
    expect(result.distribution.map((item) => item.outcome)).toEqual([
      'success',
      'failure',
      'partial',
      'unknown',
      'unfinished',
    ])
    expect(result.distribution.map((item) => item.count)).toEqual([
      0, 0, 3, 0, 2,
    ])
  })

  it('handles empty or malformed payloads', () => {
    const empty = normalizeOutcomeDistribution(undefined, 'uri')
    expect(empty.distribution).toHaveLength(5)
    expect(empty.distribution.every((item) => item.count === 0)).toBe(true)
  })
})

describe('resolveTimeRange', () => {
  it('omits date bounds for all time', () => {
    expect(resolveTimeRange(0)).toEqual({})
  })

  it('uses inclusive UTC days across a month boundary', () => {
    expect(resolveTimeRange(3, new Date('2026-09-01T22:00:00Z'))).toEqual({
      startDate: '2026-08-30',
      endDate: '2026-09-01',
    })
  })
})
