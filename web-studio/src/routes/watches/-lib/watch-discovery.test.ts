import { describe, expect, it } from 'vitest'

import type { WatchTask } from './api'
import {
  getWatchRefetchInterval,
  hasActiveWatchProcessing,
  hasCompletedTriggeredWatchSync,
  hasDiscoveredWatch,
  hasWatchExecutionAdvanced,
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

  it('keeps the syncing state while resource processing is active', () => {
    expect(hasActiveWatchProcessing([{ status: 'running' }])).toBe(true)
    expect(hasActiveWatchProcessing([{ status: 'completed' }])).toBe(false)
  })

  it('finishes only after a newly triggered processing task is terminal', () => {
    const baselineTaskIds = ['existing-task']
    expect(
      hasCompletedTriggeredWatchSync(
        [{ status: 'completed', task_id: 'existing-task' }],
        baselineTaskIds,
      ),
    ).toBe(false)
    expect(
      hasCompletedTriggeredWatchSync(
        [{ status: 'running', task_id: 'triggered-task' }],
        baselineTaskIds,
      ),
    ).toBe(false)
    expect(
      hasCompletedTriggeredWatchSync(
        [{ status: 'completed', task_id: 'triggered-task' }],
        baselineTaskIds,
      ),
    ).toBe(true)
  })

  it('detects completion from a newer watch execution time', () => {
    expect(hasWatchExecutionAdvanced(null, null)).toBe(false)
    expect(
      hasWatchExecutionAdvanced('2026-08-10T01:00:00Z', '2026-08-10T01:00:00Z'),
    ).toBe(false)
    expect(
      hasWatchExecutionAdvanced('2026-08-10T01:01:00Z', '2026-08-10T01:00:00Z'),
    ).toBe(true)
  })
})
