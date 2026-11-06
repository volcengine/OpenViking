import { normalizeReadContent } from '#/shared/lib/viking-fs'
import type { TaskRecord } from '#/features/workbench/tasks/lib/task-record'
import i18n from '#/i18n/core'
import {
  getOvResult,
  ovClient,
  OvClientError,
} from '#/shared/api/ov-client/index'
import { splitMarkdownFrontmatter } from '#/shared/lib/markdown-frontmatter'
import { parse as parseYaml } from 'yaml'

export type CompileRequest = {
  from: string[]
  to: string
  skill: string
  instruction?: string | null
  args?: Record<string, unknown>
}
/** A caller-selected name identifies reusable Compile inputs for the current account and user. */
export type CompileCreateRequest = CompileRequest & { compile_name: string }
export type UpdateSchedule =
  | { frequency: 'daily'; hour: number; minute: number }
  | { frequency: 'weekly'; weekday: number; hour: number; minute: number }
/** Public persistent inputs and acceptance times; credentials stay on the server. */
export type CompileConfig = CompileRequest & {
  compile_name: string
  auto_update: boolean
  update_schedule: UpdateSchedule | null
  /** Unix seconds of the first accepted execution; null until any run is accepted. */
  task_first_create_time: number | null
  /** Unix seconds of the latest newly accepted execution; retries do not refresh it. */
  last_run_time: number | null
  last_triggered_slot: string | null
  next_triggered_slot: string | null
}
export type CompileConfigSummary = CompileConfig
export type CompileConfigUpdate = CompileRequest & {
  compile_name: string
  auto_update: boolean
  update_schedule: UpdateSchedule | null
}
export type CompileExecutionRequest = CompileRequest | { compile_name: string }
export type CompileCreated = { compile_name: string }
export function fetchCompileConfigs(cursor?: string, signal?: AbortSignal) {
  return getOvResult<{
    configs: CompileConfigSummary[]
    next_cursor: string | null
  }>(
    ovClient.client.get({
      url: '/api/v1/compile/configs',
      signal,
      query: { limit: 30, ...(cursor ? { cursor } : {}) },
    }),
  )
}
export function fetchCompileConfig(compileName: string, signal?: AbortSignal) {
  return getOvResult<CompileConfig>(
    ovClient.client.get({
      url: '/api/v1/compile/config',
      signal,
      query: { compile_name: compileName },
    }),
  )
}
export function updateCompileConfig(body: CompileConfigUpdate) {
  return getOvResult<CompileConfig>(
    ovClient.client.post({
      url: '/api/v1/compile/config/update',
      body,
    }),
  )
}
export type CompileTask = TaskRecord & {
  task_id: string
  meta?: { request?: CompileRequest & { compile?: { compile_name?: string } } }
}
type CompileTaskPayload = Omit<CompileTask, 'meta'> & {
  meta?: {
    request?: Omit<CompileRequest, 'from'> & {
      from?: string[] | null
      compile?: { compile_name?: string }
    }
  }
}
export type TaskPage = {
  items: CompileTask[]
  next_cursor: string | null
  has_more: boolean
}
type TaskPagePayload = Omit<TaskPage, 'items'> & { items: CompileTaskPayload[] }

const normalizeCompileTask = (task: CompileTaskPayload): CompileTask => {
  const request = task.meta?.request
  if (!request) return task as CompileTask
  return {
    ...task,
    meta: { ...task.meta, request: { ...request, from: request.from ?? [] } },
  }
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
  const page = await getOvResult<TaskPagePayload>(
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
      message: i18n.t('compile:errors.upgrade'),
    })
  return { ...page, items: page.items.map(normalizeCompileTask) }
}
export async function fetchCompileTask(id: string, signal?: AbortSignal) {
  return normalizeCompileTask(
    await getOvResult<CompileTaskPayload>(
      ovClient.client.get({
        url: `/api/v1/tasks/${encodeURIComponent(id)}`,
        signal,
        query: { include_events: true },
      }),
    ),
  )
}
/** Save reusable inputs without creating an execution or source snapshot. */
export function createCompileTask(
  body: CompileCreateRequest,
): Promise<CompileCreated> {
  return getOvResult<CompileCreated>(
    ovClient.client.post({
      url: '/api/v1/compile/create',
      body,
    }),
  )
}
export async function createCompile(
  body: CompileExecutionRequest,
  key: string,
): Promise<CompileTask> {
  return normalizeCompileTask(
    await getOvResult<CompileTaskPayload>(
      ovClient.client.post({
        url: '/api/v1/compile',
        body,
        headers: { 'Idempotency-Key': key },
      }),
    ),
  )
}
export async function cancelCompile(id: string) {
  return normalizeCompileTask(
    await getOvResult<CompileTaskPayload>(
      ovClient.client.post({
        url: `/api/v1/tasks/${encodeURIComponent(id)}/cancel`,
      }),
    ),
  )
}
export function fetchCapabilities(signal?: AbortSignal) {
  return getOvResult<{
    configured: boolean
    can_create: boolean
    reason_code?: string
  }>(ovClient.client.get({ url: '/api/v1/compile/capabilities', signal }))
}
export async function lookupSubmission(key: string) {
  return normalizeCompileTask(
    await getOvResult<CompileTaskPayload>(
      ovClient.client.get({
        url: `/api/v1/compile/submissions/${encodeURIComponent(key)}`,
      }),
    ),
  )
}
export const BUILTIN_SKILLS_ROOT = 'viking://system/compile/example-skills'
export type CompileSkill = {
  name: string
  uri: string
  description: string
  metadataError?: boolean
}
export async function fetchCompileSkills(
  signal?: AbortSignal,
  targetUri?: string,
): Promise<CompileSkill[]> {
  const result = await getOvResult<{
    skills: Array<{
      name: string
      uri?: string
      root_uri?: string
      description?: string
    }>
  }>(
    ovClient.client.get({
      url: '/api/v1/skills',
      signal,
      ...(targetUri ? { query: { target_uri: targetUri } } : {}),
    }),
  )
  const skills = result.skills.flatMap((s) => {
    const uri = s.uri || s.root_uri
    return uri ? [{ name: s.name, uri, description: s.description || '' }] : []
  })
  if (targetUri !== BUILTIN_SKILLS_ROOT) return skills
  signal?.throwIfAborted()
  // System assets are file-only: their descriptions do not have abstract sidecars.
  // Read the definition supplied by this server, including skills unknown to Studio.
  return Promise.all(
    skills.map(async (skill): Promise<CompileSkill> => {
      if (skill.description) return skill
      try {
        const content = await getOvResult<unknown>(
          ovClient.client.get({
            url: '/api/v1/content/read',
            signal,
            query: {
              uri: `${skill.uri.replace(/\/$/, '')}/SKILL.md`,
              limit: 100,
            },
          }),
        )
        const frontmatter = splitMarkdownFrontmatter(
          normalizeReadContent(content),
        )
        const meta = frontmatter
          ? parseYaml(
              frontmatter.rawFrontmatter.split('\n').slice(1, -1).join('\n'),
            )
          : null
        if (
          !meta ||
          typeof meta.description !== 'string' ||
          !meta.description.trim()
        )
          return { ...skill, metadataError: true }
        return {
          ...skill,
          name:
            typeof meta.name === 'string' && meta.name.trim()
              ? meta.name
              : skill.name,
          description: meta.description,
        }
      } catch (error) {
        if (signal?.aborted) throw error
        return { ...skill, metadataError: true }
      }
    }),
  )
}
