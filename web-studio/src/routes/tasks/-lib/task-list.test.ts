import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchTasks } from './task-list'

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
    clientMocks.getTasks.mockResolvedValue({
      items: [
        {
          created_at: 2,
          task_id: 'new-task',
        },
        {
          created_at: 1,
          task_id: 'old-task',
        },
      ],
      has_more: true,
      next_cursor: 'next',
    })

    await expect(fetchTasks('all', 'all', undefined, 50)).resolves.toEqual({
      items: [
        expect.objectContaining({ task_id: 'new-task' }),
        expect.objectContaining({ task_id: 'old-task' }),
      ],
      hasMore: true,
      nextCursor: 'next',
    })
    expect(clientMocks.getTasks).toHaveBeenCalledWith({
      query: { limit: 50 },
    })
  })

  it('uses the generated client contract for task-type filters', async () => {
    clientMocks.getTasks.mockResolvedValue({
      items: [],
      has_more: false,
      next_cursor: null,
    })

    await fetchTasks('session_commit', 'all', 'cursor-1', 25)

    expect(clientMocks.getTasks).toHaveBeenCalledWith({
      query: {
        cursor: 'cursor-1',
        limit: 25,
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
      ...Array.from({ length: 200 }, (_, index) => ({
        task_id: `completed-${index}`,
        task_type: 'session_commit',
        status: 'completed',
        created_at: 201 - index,
      })),
      failedTask,
    ]
    clientMocks.getTasks.mockImplementation(({ query }) => ({
      items: records
        .filter((task) => !query.status || task.status === query.status)
        .filter(
          (task) => !query.task_type || task.task_type === query.task_type,
        )
        .slice(0, query.limit),
      has_more: false,
      next_cursor: null,
    }))

    await expect(
      fetchTasks('session_commit', 'failed', undefined, 50),
    ).resolves.toEqual({
      items: [failedTask],
      hasMore: false,
      nextCursor: null,
    })
    expect(clientMocks.getTasks).toHaveBeenCalledWith({
      query: {
        limit: 50,
        task_type: 'session_commit',
        status: 'failed',
      },
    })
  })

  it('propagates request failures to the query error state', async () => {
    clientMocks.getTasks.mockRejectedValue(new Error('request failed'))

    await expect(fetchTasks('all', 'all', undefined, 50)).rejects.toThrow(
      'request failed',
    )
  })
})
