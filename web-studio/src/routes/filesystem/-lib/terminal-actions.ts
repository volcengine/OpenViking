import { tokenize } from '#/routes/compile/-lib/commands'

export class TerminalActionError extends Error {
  constructor(
    readonly code:
      | 'addMemoryUsage'
      | 'addSkillUsage'
      | 'addResourceUsage'
      | 'writeUsage',
  ) {
    super(code)
  }
}

export async function submitMemoryCommand(
  content: string,
  sessionId: string | undefined,
  isActive: () => boolean,
  actions: {
    createSession: () => Promise<{ session_id: string }>
    addMessage: (id: string, role: 'user', content: string) => Promise<unknown>
    commitSession: (id: string) => Promise<{ task_id?: string | null }>
  },
) {
  const id = sessionId ?? (await actions.createSession()).session_id
  if (!isActive()) return
  await actions.addMessage(id, 'user', content)
  if (!isActive()) return
  const result = await actions.commitSession(id)
  if (!isActive()) return
  return { id, result }
}

export type ResourceTaskState = {
  status: string
  error?: string | null
  resource_id?: string | null
  result?: Record<string, unknown> | null
}

export function taskResultResourceUri(
  task: ResourceTaskState,
  fallbackUri?: string,
): string | undefined {
  const result = task.result ?? {}
  for (const value of [
    result.root_uri,
    result.rootUri,
    result.skill_uri,
    result.skillUri,
    result.uri,
    task.resource_id,
    fallbackUri,
  ]) {
    if (typeof value === 'string' && value.startsWith('viking://')) return value
  }
  return undefined
}

export function taskResultDetails(
  taskId: string,
  task: ResourceTaskState,
  fallbackRootUri?: string,
): Array<{ key: string; value: string; uri?: string }> {
  const result = task.result ?? {}
  const details: Array<{ key: string; value: string; uri?: string }> = [
    { key: 'taskId', value: taskId },
  ]
  for (const [key, field] of [
    ['rootUri', 'root_uri'],
    ['uri', 'uri'],
    ['name', 'name'],
    ['auxiliaryFiles', 'auxiliary_files'],
  ] as const) {
    const value =
      key === 'rootUri' ? (result[field] ?? fallbackRootUri) : result[field]
    if (typeof value === 'string' || typeof value === 'number') {
      const text = String(value)
      details.push({
        key,
        value: text,
        ...(text.startsWith('viking://') ? { uri: text } : {}),
      })
    }
  }
  return details
}

export async function waitForResourceTask(
  taskId: string,
  fetchTask: (taskId: string) => Promise<ResourceTaskState>,
  options: {
    signal?: AbortSignal
    intervalMs?: number
    maxChecks?: number
    wait?: (ms: number) => Promise<void>
  } = {},
): Promise<ResourceTaskState | null> {
  const {
    signal,
    intervalMs = 3000,
    maxChecks = 200,
    wait = (ms) => new Promise<void>((resolve) => setTimeout(resolve, ms)),
  } = options
  for (let check = 0; check < maxChecks; check++) {
    if (signal?.aborted) return null
    const task = await fetchTask(taskId)
    if (['completed', 'failed', 'cancelled'].includes(task.status)) return task
    if (check < maxChecks - 1) await wait(intervalMs)
  }
  return null
}

export function parseAddMemory(input: string) {
  const tokens = tokenize(input)
  const flagIndex = tokens.indexOf('--session-id')
  const content = tokens
    .slice(1, flagIndex < 0 ? undefined : flagIndex)
    .join(' ')
  const sessionId = flagIndex < 0 ? '' : tokens[flagIndex + 1]
  if (
    tokens[0] !== 'add-memory' ||
    !content.trim() ||
    (flagIndex >= 0 && (!sessionId || flagIndex !== tokens.length - 2))
  )
    throw new TerminalActionError('addMemoryUsage')
  return { content, sessionId: sessionId || undefined }
}

export function parseAddSkill(input: string) {
  const tokens = tokenize(input)
  const source = tokens[1]
  const flag = tokens[2]
  const targetUri =
    flag === '-p' || flag === '--parent-auto-create'
      ? tokens[3]
      : flag === '--scope'
        ? tokens[3] === 'agent'
          ? 'viking://agent/skills'
          : tokens[3] === 'user'
            ? 'viking://~/skills'
            : undefined
        : 'viking://~/skills'
  if (
    tokens[0] !== 'add-skill' ||
    !source ||
    !/^(https?:\/\/|git@)/.test(source) ||
    !targetUri?.startsWith('viking://') ||
    tokens.length !== (flag ? 4 : 2)
  )
    throw new TerminalActionError('addSkillUsage')
  return { source, targetUri }
}

export function parseAddResource(input: string) {
  const tokens = tokenize(input)
  if (tokens[0] !== 'add-resource' || tokens.length < 2)
    throw new TerminalActionError('addResourceUsage')
  const path = tokens[1]
  if (!/^https?:\/\//.test(path))
    throw new TerminalActionError('addResourceUsage')
  const options: {
    path: string
    to?: string
    parent?: string
    reason?: string
    args?: Record<string, unknown>
  } = { path }
  for (let i = 2; i < tokens.length; i += 2) {
    const flag = tokens[i]
    const value = tokens[i + 1]
    if (!value || value.startsWith('--'))
      throw new TerminalActionError('addResourceUsage')
    if (flag === '--to') options.to = value
    else if (flag === '--parent') options.parent = value
    else if (flag === '--reason') options.reason = value
    else if (flag === '--args') {
      try {
        const args: unknown = JSON.parse(value)
        if (!args || typeof args !== 'object' || Array.isArray(args))
          throw new TerminalActionError('addResourceUsage')
        options.args = args as Record<string, unknown>
      } catch {
        throw new TerminalActionError('addResourceUsage')
      }
    } else throw new TerminalActionError('addResourceUsage')
  }
  if (
    (options.to && options.parent) ||
    [options.to, options.parent].some(
      (uri) => uri && !uri.startsWith('viking://'),
    )
  )
    throw new TerminalActionError('addResourceUsage')
  return options
}

export function parseWrite(input: string) {
  const tokens = tokenize(input)
  if (tokens[0] !== 'write' || !tokens[1]?.startsWith('viking://'))
    throw new TerminalActionError('writeUsage')
  let content: string | undefined
  let mode: 'replace' | 'append' | 'create' = 'replace'
  let wait = false
  for (let i = 2; i < tokens.length; i++) {
    const flag = tokens[i]
    if (flag === '--append') {
      mode = 'append'
      continue
    }
    if (flag === '--wait') {
      wait = true
      continue
    }
    const value = tokens[++i]
    if (!value) throw new TerminalActionError('writeUsage')
    if (flag === '--content') content = value
    else if (
      flag === '--mode' &&
      ['replace', 'append', 'create'].includes(value)
    )
      mode = value as typeof mode
    else throw new TerminalActionError('writeUsage')
  }
  if (content === undefined) throw new TerminalActionError('writeUsage')
  return { uri: tokens[1], content, mode, wait }
}
