import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchTasks, MAX_TASKS } from './task-list'

const clientMocks = vi.hoisted(() => ({
  getTasks: vi.fn(),
}))

vi.mock('#/lib/ov-client', () => ({
  getOvResult: async (value: unknown) => value,
  getTasks: clientMocks.getTasks,
}))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('task list requests', () => {
  it('uses the server limit without filtering older tasks locally', async () => {
    clientMocks.getTasks.mockResolvedValue([
      {
        created_at: 2,
        task_id: 'new-task',
      },
      {
        created_at: 1,
        task_id: 'old-task',
      },
    ])

    await expect(fetchTasks('all', 'all')).resolves.toEqual([
      expect.objectContaining({ task_id: 'new-task' }),
      expect.objectContaining({ task_id: 'old-task' }),
    ])
    expect(clientMocks.getTasks).toHaveBeenCalledWith({
      query: { limit: MAX_TASKS },
    })
    expect(MAX_TASKS).toBe(200)
  })

  it('uses the generated client contract for task-type filters', async () => {
    clientMocks.getTasks.mockResolvedValue([])

    await fetchTasks('session_commit', 'all')

    expect(clientMocks.getTasks).toHaveBeenCalledWith({
      query: {
        limit: MAX_TASKS,
        task_type: 'session_commit',
      },
    })
  })

  it('finds matching tasks before the server applies its result limit', async () => {
    const failedTask = {
      task_id: 'old-failure',
      task_type: 'session_commit',
      status: 'failed',
      created_at: 1,
    }
    const records = [
      ...Array.from({ length: MAX_TASKS }, (_, index) => ({
        task_id: `completed-${index}`,
        task_type: 'session_commit',
        status: 'completed',
        created_at: MAX_TASKS + 1 - index,
      })),
      failedTask,
    ]
    clientMocks.getTasks.mockImplementation(({ query }) =>
      records
        .filter((task) => !query.status || task.status === query.status)
        .filter(
          (task) => !query.task_type || task.task_type === query.task_type,
        )
        .slice(0, query.limit),
    )

    await expect(fetchTasks('session_commit', 'failed')).resolves.toEqual([
      failedTask,
    ])
    expect(clientMocks.getTasks).toHaveBeenCalledWith({
      query: {
        limit: MAX_TASKS,
        task_type: 'session_commit',
        status: 'failed',
      },
    })
  })

  it('propagates request failures to the query error state', async () => {
    clientMocks.getTasks.mockRejectedValue(new Error('request failed'))

    await expect(fetchTasks('all', 'all')).rejects.toThrow('request failed')
  })
})
