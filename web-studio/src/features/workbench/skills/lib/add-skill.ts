import type { SkillScope } from '#/features/workbench/skills/components/skill-scope-tabs'
import { getOvResult, ovClient } from '#/shared/api/ov-client/index'

type ValidationIssue = { message?: string; rule?: string }

export type SkillValidation = {
  valid: boolean
  name?: string
  errors: ValidationIssue[]
  warnings: ValidationIssue[]
}

export type SkillTask = {
  task_id: string
  status:
    | 'pending'
    | 'running'
    | 'cancelling'
    | 'completed'
    | 'failed'
    | 'cancelled'
  error?: string | null
}

export async function validateNewSkill(data: string): Promise<SkillValidation> {
  return getOvResult<SkillValidation>(
    ovClient.client.post({
      url: '/api/v1/skills/validate',
      body: { data },
    }),
  )
}

export async function addNewSkill(
  data: string,
  scope: SkillScope | `viking://${string}`,
): Promise<{ task_id?: string }> {
  return getOvResult<{ task_id?: string }>(
    ovClient.client.post({
      url: '/api/v1/skills',
      body: {
        data,
        target_uri:
          scope === 'agent'
            ? 'viking://agent/skills'
            : scope === 'user'
              ? 'viking://~/skills'
              : scope,
      },
    }),
  )
}

export async function fetchSkillTask(taskId: string): Promise<SkillTask> {
  return getOvResult<SkillTask>(
    ovClient.client.get({
      url: `/api/v1/tasks/${encodeURIComponent(taskId)}`,
    }),
  )
}
