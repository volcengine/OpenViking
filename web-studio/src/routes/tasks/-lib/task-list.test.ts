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

  it('keeps effective status filtering on the client', async () => {
    clientMocks.getTasks.mockResolvedValue(
      Array.from({ length: 9 }, (_, index) => ({
        created_at: index + 1,
        status: 'running',
        task_id: `task-${index + 1}`,
      })),
    )

    await expect(fetchTasks('all', 'pending')).resolves.toEqual([
      expect.objectContaining({ task_id: 'task-9' }),
    ])
    expect(clientMocks.getTasks).toHaveBeenCalledWith({
      query: { limit: MAX_TASKS },
    })
  })

  it('propagates request failures to the query error state', async () => {
    clientMocks.getTasks.mockRejectedValue(new Error('request failed'))

    await expect(fetchTasks('all', 'all')).rejects.toThrow('request failed')
  })
})
