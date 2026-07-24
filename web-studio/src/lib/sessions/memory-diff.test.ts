import { describe, expect, it } from 'vitest'

import { parseSessionMemoryDiff } from './memory-diff'

describe('parseSessionMemoryDiff', () => {
  it('normalizes real memory operations and derives the summary from them', () => {
    expect(
      parseSessionMemoryDiff(
        JSON.stringify({
          archive_uri:
            'viking://user/alice/sessions/session-1/history/archive_002',
          extracted_at: '2026-07-24T08:00:00Z',
          operations: {
            adds: [
              {
                after: 'new preference',
                memory_type: 'preferences',
                uri: 'viking://user/alice/memories/preferences/editor.md',
              },
            ],
            updates: [
              {
                after: 'new profile',
                before: 'old profile',
                memory_type: 'profile',
                uri: 'viking://user/alice/memories/profile.md',
              },
            ],
            deletes: [],
          },
          summary: {
            total_adds: 99,
            total_deletes: 99,
            total_updates: 99,
          },
        }),
        'archive_002',
      ),
    ).toMatchObject({
      archiveId: 'archive_002',
      extractedAt: '2026-07-24T08:00:00Z',
      summary: { adds: 1, deletes: 0, updates: 1 },
    })
  })

  it('returns null for an empty or invalid diff', () => {
    expect(
      parseSessionMemoryDiff(
        {
          operations: { adds: [], deletes: [], updates: [] },
        },
        'archive_001',
      ),
    ).toBeNull()
    expect(parseSessionMemoryDiff('not-json', 'archive_001')).toBeNull()
  })
})
