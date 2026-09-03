import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchTask } from './task-detail-sheet'

const clientMocks = vi.hoisted(() => ({
  getTaskByTaskId: vi.fn(),
}))

vi.mock('#/lib/ov-client', () => ({
  getOvResult: async (value: unknown) => value,
  getTaskByTaskId: clientMocks.getTaskByTaskId,
  isOvClientError: (error: unknown) =>
    typeof error === 'object' && error !== null && 'code' in error,
}))

beforeEach(() => {
  vi.clearAllMocks()
  if (typeof localStorage !== 'undefined') {
    localStorage.clear()
  }
})

describe('fetchTask', () => {
  it('translates only an explicit task not-found response', async () => {
    clientMocks.getTaskByTaskId.mockRejectedValue({
      code: 'NOT_FOUND',
      message: 'Task not found or expired',
    })

    await expect(
      fetchTask('missing-task', '任务不存在或已过期'),
    ).rejects.toThrow('任务不存在或已过期')
  })

  it('preserves other backend failures for the query error state', async () => {
    const backendError = Object.assign(new Error('gateway unavailable'), {
      code: 'INTERNAL_ERROR',
    })
    clientMocks.getTaskByTaskId.mockRejectedValue(backendError)

    await expect(fetchTask('task-1', '任务不存在或已过期')).rejects.toBe(
      backendError,
    )
  })
})
