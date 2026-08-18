import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  deleteWatch,
  fetchWatchProcessingHistory,
  normalizeWatchList,
  normalizeWatchTask,
  triggerWatch,
  updateWatch,
} from './api'

const clientMocks = vi.hoisted(() => ({
  delete: vi.fn(),
  get: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
  getTasks: vi.fn(),
}))

vi.mock('#/lib/ov-client', () => ({
  getOvResult: async (value: unknown) => value,
  getTasks: clientMocks.getTasks,
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

describe('watch API operations', () => {
  it('queries resource processing history by target URI', async () => {
    clientMocks.getTasks.mockResolvedValue([])

    await fetchWatchProcessingHistory('viking://resources/OpenViking')

    expect(clientMocks.getTasks).toHaveBeenCalledWith({
      query: {
        limit: 200,
        resource_id: 'viking://resources/OpenViking',
        task_type: 'add_resource',
      },
    })
  })

  it('maps update fields to the REST request body', async () => {
    clientMocks.patch.mockResolvedValue(watch)

    await updateWatch('watch/1', { isActive: false, watchInterval: 60 })

    expect(clientMocks.patch).toHaveBeenCalledWith({
      body: { is_active: false, watch_interval: 60 },
      url: '/api/v1/watches/watch%2F1',
    })
  })

  it('triggers an immediate sync by encoded task ID', async () => {
    clientMocks.post.mockResolvedValue({ scheduled: true })

    await triggerWatch('watch/1')

    expect(clientMocks.post).toHaveBeenCalledWith({
      url: '/api/v1/watches/watch%2F1/trigger',
    })
  })

  it('deletes a watch by encoded task ID', async () => {
    clientMocks.delete.mockResolvedValue({ deleted: true })

    await deleteWatch('watch/1')

    expect(clientMocks.delete).toHaveBeenCalledWith({
      url: '/api/v1/watches/watch%2F1',
    })
  })
})
