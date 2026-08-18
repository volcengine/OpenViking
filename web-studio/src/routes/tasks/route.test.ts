import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchTasks } from './route'

const ovClientMocks = vi.hoisted(() => ({
  getOvResult: vi.fn(),
  getTasks: vi.fn(),
}))

vi.mock('#/lib/ov-client', () => ({
  getOvResult: ovClientMocks.getOvResult,
  getTasks: ovClientMocks.getTasks,
  ovClient: {},
}))

beforeEach(() => {
  ovClientMocks.getOvResult.mockReset()
  ovClientMocks.getTasks.mockReset()
  ovClientMocks.getOvResult.mockImplementation(async (request) => request)
})

describe('fetchTasks', () => {
  it('fetches the latest 300 tasks in API-sized pages', async () => {
    const now = Math.floor(Date.now() / 1000)
    ovClientMocks.getTasks
      .mockResolvedValueOnce(
        Array.from({ length: 200 }, (_, index) => ({
          created_at: now,
          task_id: `task-${index}`,
        })),
      )
      .mockResolvedValueOnce(
        Array.from({ length: 100 }, (_, index) => ({
          created_at: now,
          task_id: `task-${index + 200}`,
        })),
      )

    const tasks = await fetchTasks('all', 'all', '24h')

    expect(tasks).toHaveLength(300)
    expect(ovClientMocks.getTasks).toHaveBeenNthCalledWith(1, {
      query: {
        limit: 200,
        offset: 0,
        status: undefined,
        task_type: undefined,
      },
    })
    expect(ovClientMocks.getTasks).toHaveBeenNthCalledWith(2, {
      query: {
        limit: 100,
        offset: 200,
        status: undefined,
        task_type: undefined,
      },
    })
  })

  it('continues loading all task pages until the API returns a short page', async () => {
    ovClientMocks.getTasks
      .mockResolvedValueOnce(
        Array.from({ length: 200 }, (_, index) => ({
          task_id: `task-${index}`,
        })),
      )
      .mockResolvedValueOnce([{ task_id: 'task-200' }])

    const tasks = await fetchTasks('all', 'all', 'all')

    expect(tasks).toHaveLength(201)
    expect(ovClientMocks.getTasks).toHaveBeenNthCalledWith(2, {
      query: {
        limit: 200,
        offset: 200,
        status: undefined,
        task_type: undefined,
      },
    })
    expect(ovClientMocks.getTasks).toHaveBeenCalledTimes(2)
  })

  it('passes task type filters through every page', async () => {
    ovClientMocks.getTasks.mockResolvedValue([])

    await fetchTasks('session_commit', 'all', 'all')

    expect(ovClientMocks.getTasks).toHaveBeenCalledWith({
      query: {
        limit: 200,
        offset: 0,
        status: undefined,
        task_type: 'session_commit',
      },
    })
  })

  it('stops when an older API repeats the first page', async () => {
    const page = Array.from({ length: 200 }, (_, index) => ({
      task_id: `task-${index}`,
    }))
    ovClientMocks.getTasks.mockResolvedValue(page)

    const tasks = await fetchTasks('all', 'all', 'all')

    expect(tasks).toHaveLength(200)
    expect(ovClientMocks.getTasks).toHaveBeenCalledTimes(2)
  })
})
