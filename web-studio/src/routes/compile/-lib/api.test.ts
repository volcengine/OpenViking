import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchCompileTask, fetchCompileTasks } from './api'

const clientMocks = vi.hoisted(() => ({
  get: vi.fn(),
}))

vi.mock('#/lib/ov-client', () => ({
  getOvResult: async (value: unknown) => value,
  OvClientError: class extends Error {},
  ovClient: { client: clientMocks },
}))

const taskWithoutSources = {
  task_id: 'cmp_memory',
  task_type: 'compile',
  status: 'failed',
  meta: {
    request: {
      to: 'viking://user/default/memories/events',
      skill: 'memory',
    },
  },
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('compile task API normalization', () => {
  it('normalizes an omitted source list in task pages', async () => {
    clientMocks.get.mockResolvedValue({
      items: [taskWithoutSources],
      next_cursor: null,
      has_more: false,
    })

    const page = await fetchCompileTasks()

    expect(page.items[0].meta?.request?.from).toEqual([])
    expect(taskWithoutSources.meta.request).not.toHaveProperty('from')
  })

  it('normalizes a null source list in task details', async () => {
    clientMocks.get.mockResolvedValue({
      ...taskWithoutSources,
      meta: {
        request: { ...taskWithoutSources.meta.request, from: null },
      },
    })

    const task = await fetchCompileTask('cmp_memory')

    expect(task.meta?.request?.from).toEqual([])
  })
})
