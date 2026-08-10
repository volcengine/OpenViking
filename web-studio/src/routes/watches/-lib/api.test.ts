import { beforeEach, describe, expect, it, vi } from 'vitest'

import { normalizeWatchList, normalizeWatchTask } from './api'

const clientMocks = vi.hoisted(() => ({
  get: vi.fn(),
}))

vi.mock('#/lib/ov-client', () => ({
  getOvResult: async (value: unknown) => value,
  ovClient: { client: clientMocks },
}))

const watch = {
  created_at: '2026-08-10T10:00:00',
  instruction: 'Keep the docs current',
  is_active: true,
  last_execution_time: null,
  next_execution_time: '2026-08-11T10:00:00',
  path: 'https://github.com/volcengine/OpenViking',
  reason: 'Track upstream changes',
  task_id: 'watch-1',
  to_uri: 'viking://resources/OpenViking',
  watch_interval: 1440,
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('watch API normalization', () => {
  it('normalizes a watch task', () => {
    expect(normalizeWatchTask(watch)).toEqual({
      createdAt: '2026-08-10T10:00:00',
      instruction: 'Keep the docs current',
      isActive: true,
      lastExecutionTime: null,
      nextExecutionTime: '2026-08-11T10:00:00',
      path: 'https://github.com/volcengine/OpenViking',
      reason: 'Track upstream changes',
      taskId: 'watch-1',
      toUri: 'viking://resources/OpenViking',
      watchInterval: 1440,
    })
  })

  it('drops malformed tasks from list responses', () => {
    expect(
      normalizeWatchList({ tasks: [watch, { task_id: 'invalid' }] }),
    ).toEqual([normalizeWatchTask(watch)])
  })
})
