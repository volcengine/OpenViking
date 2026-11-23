import {
  addNewSkill,
  fetchSkillTask,
  validateNewSkill,
} from '#/features/workbench/skills/lib/add-skill'
import { ovClient } from '#/shared/api/ov-client/index'
import { beforeEach, expect, it, vi } from 'vitest'

vi.mock('#/shared/api/ov-client/index', () => ({
  ovClient: { client: { post: vi.fn(), get: vi.fn() } },
  getOvResult: async (request: Promise<{ data: { result: unknown } }>) =>
    (await request).data.result,
}))

beforeEach(() => {
  vi.resetAllMocks()
})

it('validates content before adding and sends explicit private or shared targets', async () => {
  vi.mocked(ovClient.client.post).mockResolvedValue({
    data: { result: { valid: true, errors: [], warnings: [] } },
  } as never)
  await validateNewSkill('---\nname: example\n---')
  expect(ovClient.client.post).toHaveBeenCalledWith({
    url: '/api/v1/skills/validate',
    body: { data: '---\nname: example\n---' },
  })

  await addNewSkill('private content', 'user')
  await addNewSkill('shared content', 'agent')
  expect(ovClient.client.post).toHaveBeenNthCalledWith(2, {
    url: '/api/v1/skills',
    body: { data: 'private content', target_uri: 'viking://~/skills' },
  })
  expect(ovClient.client.post).toHaveBeenNthCalledWith(3, {
    url: '/api/v1/skills',
    body: { data: 'shared content', target_uri: 'viking://agent/skills' },
  })
})

it('reads the submitted background task by its ID', async () => {
  vi.mocked(ovClient.client.get).mockResolvedValue({
    data: { result: { task_id: 'task-1', status: 'completed' } },
  } as never)
  await expect(fetchSkillTask('task-1')).resolves.toMatchObject({
    status: 'completed',
  })
  expect(ovClient.client.get).toHaveBeenCalledWith({
    url: '/api/v1/tasks/task-1',
  })
})
