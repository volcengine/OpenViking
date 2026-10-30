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
  instruction?: string
  args?: Record<string, unknown>
}
export type CompileTask = TaskRecord & {
  task_id: string
  meta?: { request?: CompileRequest }
}
type CompileTaskPayload = Omit<CompileTask, 'meta'> & {
  meta?: {
    request?: Omit<CompileRequest, 'from'> & { from?: string[] | null }
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
export async function createCompile(body: CompileRequest, key: string) {
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
