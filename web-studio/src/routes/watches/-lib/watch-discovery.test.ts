import { describe, expect, it } from 'vitest'

import type { WatchTask } from './api'
import {
  getWatchRefetchInterval,
  hasActiveWatchProcessing,
  hasCompletedWatchSync,
  hasDiscoveredWatch,
  normalizeWatchUri,
  WATCH_DISCOVERY_INTERVAL_MS,
} from './watch-discovery'

const watch: WatchTask = {
  createdAt: '2026-08-10T00:00:00Z',
  instruction: '',
  isActive: true,
  lastExecutionTime: null,
  nextExecutionTime: '2026-08-10T01:00:00Z',
  path: 'https://github.com/volcengine/OpenViking',
  reason: '',
  taskId: 'watch-1',
  toUri: 'viking://resources/OpenViking/',
  watchInterval: 60,
}

describe('watch discovery', () => {
  it('polls only while discovering a newly created watch', () => {
    expect(getWatchRefetchInterval(true)).toBe(WATCH_DISCOVERY_INTERVAL_MS)
    expect(getWatchRefetchInterval(false)).toBe(false)
  })

  it('normalizes trailing slashes before matching a created watch', () => {
    expect(normalizeWatchUri('viking://resources/OpenViking///')).toBe(
      'viking://resources/OpenViking',
    )
    expect(hasDiscoveredWatch([watch], 'viking://resources/OpenViking')).toBe(
      true,
    )
  })

  it('keeps polling when the created watch is absent', () => {
    expect(hasDiscoveredWatch([watch], 'viking://resources/another')).toBe(
      false,
    )
  })

  it('detects completion when the watch execution time changes', () => {
    expect(hasCompletedWatchSync([watch], watch.taskId, null)).toBe(false)
    expect(
      hasCompletedWatchSync(
        [{ ...watch, lastExecutionTime: '2026-08-10T01:00:00Z' }],
        watch.taskId,
        null,
      ),
    ).toBe(true)
  })

  it('keeps the syncing state while resource processing is active', () => {
    expect(hasActiveWatchProcessing([{ status: 'running' }])).toBe(true)
    expect(hasActiveWatchProcessing([{ status: 'completed' }])).toBe(false)
  })
})
