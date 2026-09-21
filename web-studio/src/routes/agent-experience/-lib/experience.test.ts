// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'

import {
  buildCustomTimeRange,
  buildExperiencesUri,
  formatFileSize,
  formatTimestamp,
  getExperienceDisplayName,
  isExperienceUpdatedSinceLastSeen,
  markExperiencesSeen,
  normalizeOutcomeDistribution,
  normalizeTrajectoryPage,
  resolveTimeRange,
} from './experience'

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

describe('getExperienceDisplayName', () => {
  it('strips the markdown extension from the file name', () => {
    expect(
      getExperienceDisplayName(
        'viking://user/default/memories/experiences/exchange.md',
      ),
    ).toBe('exchange')
    expect(
      getExperienceDisplayName(
        'viking://user/default/memories/exchanges/Refund_Flow.MD',
      ),
    ).toBe('Refund_Flow')
  })

  it('returns the raw segment when there is no extension', () => {
    expect(
      getExperienceDisplayName('viking://user/default/memories/experiences/x'),
    ).toBe('x')
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
  it('disables filtering for the all preset', () => {
    expect(resolveTimeRange('all', new Date('2026-08-15T12:00:00Z'))).toEqual({
      preset: 'all',
    })
  })

  it('returns no bounds for the custom preset', () => {
    expect(resolveTimeRange('custom')).toEqual({ preset: 'custom' })
  })

  it('computes inclusive UTC bounds for the 7d preset', () => {
    expect(resolveTimeRange('7d', new Date('2026-08-15T12:00:00Z'))).toEqual({
      preset: '7d',
      startDate: '2026-08-09',
      endDate: '2026-08-15',
    })
  })

  it('computes inclusive UTC bounds for the 30d preset', () => {
    expect(resolveTimeRange('30d', new Date('2026-08-15T12:00:00Z'))).toEqual({
      preset: '30d',
      startDate: '2026-07-17',
      endDate: '2026-08-15',
    })
  })

  it('handles month boundaries', () => {
    expect(resolveTimeRange('7d', new Date('2026-09-03T23:59:59Z'))).toEqual({
      preset: '7d',
      startDate: '2026-08-28',
      endDate: '2026-09-03',
    })
  })
})

describe('buildCustomTimeRange', () => {
  it('builds a range from both bounds', () => {
    expect(buildCustomTimeRange('2026-08-01', '2026-08-10')).toEqual({
      range: { preset: 'all', startDate: '2026-08-01', endDate: '2026-08-10' },
    })
  })

  it('supports an open-ended start bound only', () => {
    expect(buildCustomTimeRange('2026-08-01', '')).toEqual({
      range: { preset: 'all', startDate: '2026-08-01' },
    })
  })

  it('supports an open-ended end bound only', () => {
    expect(buildCustomTimeRange('', '2026-08-10')).toEqual({
      range: { preset: 'all', endDate: '2026-08-10' },
    })
  })

  it('rejects invalid formats', () => {
    expect(buildCustomTimeRange('2026/08/01', '2026-08-10')).toEqual({
      error: 'invalid',
    })
    expect(buildCustomTimeRange('2026-13-01', '')).toEqual({
      error: 'invalid',
    })
    expect(buildCustomTimeRange('', '')).toEqual({ error: 'invalid' })
  })

  it('rejects reversed ranges', () => {
    expect(buildCustomTimeRange('2026-08-10', '2026-08-01')).toEqual({
      error: 'order',
    })
  })
})

describe('last-seen tracking', () => {
  it('reports unseen experiences as updated until marked seen', () => {
    expect(
      isExperienceUpdatedSinceLastSeen(
        'viking://user/u/memories/experiences/e.md',
        '2026-08-05T02:00:00Z',
      ),
    ).toBe(true)

    markExperiencesSeen([{ uri: 'viking://user/u/memories/experiences/e.md' }])

    expect(
      isExperienceUpdatedSinceLastSeen(
        'viking://user/u/memories/experiences/e.md',
        '2026-08-05T02:00:00Z',
      ),
    ).toBe(false)
    expect(
      isExperienceUpdatedSinceLastSeen(
        'viking://user/u/memories/experiences/e.md',
        undefined,
      ),
    ).toBe(false)
  })
})

describe('formatTimestamp', () => {
  it('formats parseable timestamps', () => {
    const formatted = formatTimestamp('2026-08-05T02:00:00Z', 'zh-CN')
    expect(formatted).toBeTruthy()
    expect(formatTimestamp('2026-08-05T02:00:00Z', 'zh-CN')).toBe(formatted)
  })

  it('returns undefined for missing or invalid values', () => {
    expect(formatTimestamp(undefined, 'zh-CN')).toBeUndefined()
    expect(formatTimestamp('', 'zh-CN')).toBeUndefined()
    expect(formatTimestamp('not-a-date', 'zh-CN')).toBeUndefined()
  })
})

describe('formatFileSize', () => {
  it('formats bytes, kilobytes and megabytes', () => {
    expect(formatFileSize(0)).toBe('0 B')
    expect(formatFileSize(512)).toBe('512 B')
    expect(formatFileSize(2048)).toBe('2 KB')
    expect(formatFileSize(1024 * 1024)).toBe('1 MB')
    expect(formatFileSize(1024 * 1024 * 1024)).toBe('1 GB')
  })

  it('returns undefined for missing or invalid sizes', () => {
    expect(formatFileSize(undefined)).toBeUndefined()
    expect(formatFileSize(-1)).toBeUndefined()
    expect(formatFileSize(Number.NaN)).toBeUndefined()
  })
})
