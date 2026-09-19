import { getOvResult, ovClient, OvClientError } from '#/lib/ov-client'
import type { TaskRecord } from '#/routes/tasks/-lib/task-record'

export type CompileRequest = {
  from: string[]
  to: string
  skill: string
  instruction?: string
  args?: Record<string, unknown>
}
export type CompileTask = TaskRecord & {
  task_id: string
  meta?: { request?: CompileRequest }
}
export type TaskPage = {
  items: CompileTask[]
  next_cursor: string | null
  has_more: boolean
}
export const active = (status?: string) =>
  ['pending', 'running', 'cancelling'].includes(status ?? '')
export const taskTitle = (task: CompileTask) =>
  task.meta?.request
    ? `${task.meta.request.skill.split('/').pop()} → ${task.meta.request.to.split('/').pop()}`
    : task.task_id
export const errorText = (error: unknown) =>
  error && typeof error === 'object' && 'message' in error
    ? String(error.message)
    : String(error)
export async function fetchCompileTasks(
  status = '',
  q = '',
  cursor?: string,
  signal?: AbortSignal,
) {
  const page = await getOvResult<TaskPage>(
    ovClient.client.get({
      url: '/api/v1/tasks',
      signal,
      query: {
        task_type: 'compile',
        pagination: 'cursor',
        limit: 30,
        ...(status ? { status } : {}),
        ...(q ? { q } : {}),
        ...(cursor ? { cursor } : {}),
      },
    }),
  )
  if (!Array.isArray(page.items))
    throw new OvClientError({
      code: 'PAGINATION_UNSUPPORTED',
      message:
        'The server does not support paginated tasks. Update OpenViking and retry.',
    })
  return page
}
export function fetchCompileTask(id: string, signal?: AbortSignal) {
  return getOvResult<CompileTask>(
    ovClient.client.get({
      url: `/api/v1/tasks/${encodeURIComponent(id)}`,
      signal,
      query: { include_events: true },
    }),
  )
}
export function createCompile(body: CompileRequest, key: string) {
  return getOvResult<CompileTask>(
    ovClient.client.post({
      url: '/api/v1/compile',
      body,
      headers: { 'Idempotency-Key': key },
    }),
  )
}
export function cancelCompile(id: string) {
  return getOvResult<CompileTask>(
    ovClient.client.post({
      url: `/api/v1/tasks/${encodeURIComponent(id)}/cancel`,
    }),
  )
}
export function fetchCapabilities() {
  return getOvResult<{
    configured: boolean
    can_create: boolean
    reason_code?: string
  }>(ovClient.client.get({ url: '/api/v1/compile/capabilities' }))
}
export function lookupSubmission(key: string) {
  return getOvResult<CompileTask>(
    ovClient.client.get({
      url: `/api/v1/compile/submissions/${encodeURIComponent(key)}`,
    }),
  )
}
export type CompileSkill = { name: string; uri: string; description: string }
export async function fetchCompileSkills(
  signal?: AbortSignal,
): Promise<CompileSkill[]> {
  const result = await getOvResult<{
    skills: Array<{
      name: string
      uri?: string
      root_uri?: string
      description?: string
    }>
  }>(ovClient.client.get({ url: '/api/v1/skills', signal }))
  return result.skills.flatMap((s) => {
    const uri = s.uri || s.root_uri
    return uri ? [{ name: s.name, uri, description: s.description || '' }] : []
  })
}
