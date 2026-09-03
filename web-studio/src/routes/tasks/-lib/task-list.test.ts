import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  canCancelTask,
  cancelTask,
  fetchTasks,
  isCancellableTaskType,
  isTaskCancelling,
  MAX_TASKS,
} from './task-list'

const clientMocks = vi.hoisted(() => ({
  getTasks: vi.fn(),
  post: vi.fn(),
}))

vi.mock('#/lib/ov-client', () => ({
  getOvResult: async (value: unknown) => value,
  getTasks: clientMocks.getTasks,
  ovClient: { instance: { post: clientMocks.post } },
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

describe('cancellable task helpers', () => {
  it('mirrors the backend cancellable type whitelist', () => {
    expect(isCancellableTaskType('session_commit')).toBe(true)
    expect(isCancellableTaskType('add_resource')).toBe(true)
    expect(isCancellableTaskType('admin_reindex')).toBe(true)
    expect(isCancellableTaskType('snapshot_restore_reindex')).toBe(true)
    expect(isCancellableTaskType('add_skill')).toBe(false)
    expect(isCancellableTaskType('connector_import')).toBe(false)
    expect(isCancellableTaskType()).toBe(false)
  })

  it('only allows cancelling pending or running tasks', () => {
    const task = { task_id: 'task-1', task_type: 'session_commit' }
    expect(canCancelTask({ ...task, status: 'pending' })).toBe(true)
    expect(canCancelTask({ ...task, status: 'running' })).toBe(true)
    expect(canCancelTask({ ...task, status: 'cancelling' })).toBe(false)
    expect(canCancelTask({ ...task, status: 'completed' })).toBe(false)
    expect(canCancelTask({ ...task, status: 'failed' })).toBe(false)
    expect(canCancelTask({ ...task, status: 'cancelled' })).toBe(false)
    expect(
      canCancelTask({
        task_id: 'task-2',
        task_type: 'add_skill',
        status: 'running',
      }),
    ).toBe(false)
    expect(
      canCancelTask({ task_type: 'session_commit', status: 'running' }),
    ).toBe(false)
  })

  it('reports the cancelling intermediate state for cancellable types', () => {
    expect(
      isTaskCancelling({
        task_id: 'task-1',
        task_type: 'session_commit',
        status: 'cancelling',
      }),
    ).toBe(true)
    expect(
      isTaskCancelling({
        task_id: 'task-1',
        task_type: 'session_commit',
        status: 'running',
      }),
    ).toBe(false)
  })
})

describe('cancelTask', () => {
  it('posts to the task cancel endpoint with an escaped id', async () => {
    // The module-level mock keeps getOvResult as an identity function, so
    // the raw axios response is returned as-is here.
    const response = {
      data: {
        status: 'ok',
        result: { task_id: 'task/1', status: 'cancelling' },
      },
    }
    clientMocks.post.mockResolvedValue(response)

    await expect(cancelTask('task/1')).resolves.toBe(response)
    expect(clientMocks.post).toHaveBeenCalledWith(
      '/api/v1/tasks/task%2F1/cancel',
    )
  })

  it('propagates backend errors to the mutation error state', async () => {
    clientMocks.post.mockRejectedValue(new Error('cancel failed'))

    await expect(cancelTask('task-1')).rejects.toThrow('cancel failed')
  })
})
