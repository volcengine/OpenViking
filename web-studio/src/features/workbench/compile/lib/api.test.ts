import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  BUILTIN_SKILLS_ROOT,
  fetchCompileSkills,
  fetchCompileTask,
  fetchCompileTasks,
} from '#/features/workbench/compile/lib/api'

const clientMocks = vi.hoisted(() => ({
  get: vi.fn(),
}))

vi.mock('#/shared/api/ov-client/index', () => ({
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

describe('built-in skill discovery', () => {
  it('discovers server-provided skills and reads file-only metadata, including unknown skills', async () => {
    clientMocks.get.mockImplementation(async ({ url, query }) => {
      if (url === '/api/v1/skills') {
        expect(query.target_uri).toBe(BUILTIN_SKILLS_ROOT)
        return {
          skills: [
            {
              name: 'future-tool',
              root_uri: `${BUILTIN_SKILLS_ROOT}/future-tool`,
            },
          ],
        }
      }
      return {
        content:
          '---\nname: future-tool\ndescription: A new server-provided capability.\n---\nBody',
      }
    })
    expect(await fetchCompileSkills(undefined, BUILTIN_SKILLS_ROOT)).toEqual([
      {
        name: 'future-tool',
        uri: `${BUILTIN_SKILLS_ROOT}/future-tool`,
        description: 'A new server-provided capability.',
      },
    ])
  })

  it('keeps other skills when one definition is unreadable', async () => {
    clientMocks.get
      .mockResolvedValueOnce({
        skills: [
          {
            name: 'ready',
            uri: `${BUILTIN_SKILLS_ROOT}/ready`,
            description: 'Ready to use',
          },
          { name: 'unreadable', uri: `${BUILTIN_SKILLS_ROOT}/unreadable` },
        ],
      })
      .mockRejectedValueOnce(new Error('Unavailable'))
    const result = await fetchCompileSkills(undefined, BUILTIN_SKILLS_ROOT)
    expect(result).toHaveLength(2)
    expect(result[0].description).toBe('Ready to use')
    expect(result[1].metadataError).toBe(true)
  })

  it('does not start definition reads after a connection abort', async () => {
    const controller = new AbortController()
    clientMocks.get.mockImplementationOnce(async () => {
      controller.abort()
      return { skills: [{ name: 'wiki', uri: `${BUILTIN_SKILLS_ROOT}/wiki` }] }
    })
    await expect(
      fetchCompileSkills(controller.signal, BUILTIN_SKILLS_ROOT),
    ).rejects.toThrow()
    expect(clientMocks.get).toHaveBeenCalledOnce()
  })
})
