import type {
  Activity,
  Capabilities,
  InstanceList,
  Metrics,
  ModuleState,
  Overview,
  Page,
  Session,
  Task,
  TaskInput,
  TaskReport,
} from './types'

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public code = 'request_failed',
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

type Query = Record<string, string | number | undefined>

async function request<T>(path: string, instance?: string, query: Query = {}): Promise<T> {
  const url = new URL(path, window.location.origin)
  if (instance) url.searchParams.set('instance', instance)
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) url.searchParams.set(key, String(value))
  }
  let response: Response
  try {
    response = await fetch(url, { headers: { accept: 'application/json' }, cache: 'no-store' })
  } catch {
    throw new ApiError('Cannot reach the local console service.', 0, 'instance_unavailable')
  }
  if (!response.ok) {
    let code = response.status === 404 ? 'not_found' : 'request_failed'
    let message = `Console request failed (${response.status}).`
    try {
      const body = await response.json()
      code = body.error?.code ?? body.error ?? code
      message = body.error?.message ?? (typeof body.error === 'string' ? body.error.replaceAll('_', ' ') : message)
    } catch {
      // Keep the generic, non-sensitive error when an upstream returns no JSON.
    }
    throw new ApiError(message, response.status, code)
  }
  return response.json() as Promise<T>
}

const instancePath = (instance: string, suffix = '') =>
  `/api/v1/instances/${encodeURIComponent(instance)}${suffix}`

const READ_MODULES = [
  'instances.read', 'capabilities.read', 'runtime.read', 'sessions.read',
  'activity.read', 'tasks.read', 'metrics.read', 'audit.read',
]
const UNAVAILABLE_MODULES = [
  'tasks.control', 'runtime.control', 'sessions.control', 'configuration.read',
  'configuration.write', 'instances.setup', 'instances.process', 'providers.read',
  'providers.write', 'runtimePackages.install', 'logs.read', 'diagnostics.bundle',
  'events.stream', 'tasks.retry', 'tasks.pause',
]

export function legacyCapabilities(instance: string): Capabilities {
  const modules: Record<string, ModuleState> = {}
  for (const key of READ_MODULES) {
    const taskModule = key === 'tasks.read' || key === 'metrics.read'
    const audit = key === 'audit.read'
    const legacyRuntime = key === 'runtime.read'
    const unavailable = taskModule || audit || legacyRuntime
    modules[key] = {
      enabled: true,
      available: !unavailable,
      effective: !unavailable,
      reasonCode: taskModule ? 'workers_not_merged' : audit ? 'audit_store_unavailable' : legacyRuntime ? 'runtime_unsupported' : null,
      message: taskModule
        ? 'This instance exposes the legacy activity API. Task APIs require the workers scheduler and RFC v1 backend.'
        : legacyRuntime ? 'The legacy API exposes active turns but no verified Runtime, model, effort, or revision snapshot.' : undefined,
    }
  }
  for (const key of UNAVAILABLE_MODULES) {
    modules[key] = {
      enabled: false,
      available: false,
      effective: false,
      reasonCode: key === 'tasks.pause' ? 'not_provided' : 'disabled_by_owner',
      message: key === 'tasks.pause' ? 'Pause/resume requires durable checkpoints and is intentionally not provided.' : undefined,
    }
  }
  return { instanceId: instance, modules, source: 'legacy', generatedAt: new Date().toISOString() }
}

type LegacyActivity = {
  generatedAt: number
  service: { ready: boolean; subscriber: string; activeTurns?: number }
  overview: { attention: number; receivedToday: number; sentToday: number }
  sessions: Array<{ epoch: number; startMs?: number; endMs?: number; activityCount?: number }>
  activities: Array<Record<string, unknown> & { id: number; kind: string; createdAtMs: number }>
  nextCursor: string | null
}

function legacyActivity(value: LegacyActivity): Page<Activity> & { legacy: LegacyActivity } {
  return {
    items: value.activities.map((row) => ({
      id: String(row.id),
      kind: row.kind,
      severity: typeof row.severity === 'string' ? row.severity : undefined,
      summary: typeof row.summary === 'string' ? row.summary : undefined,
      taskId: typeof row.taskId === 'string' ? row.taskId : undefined,
      turn: typeof row.turn === 'number' ? row.turn : undefined,
      createdAt: new Date(row.createdAtMs).toISOString(),
    })),
    nextCursor: value.nextCursor,
    legacy: value,
  }
}

export const api = {
  instances: () => request<InstanceList>('/api/v1/instances'),

  async capabilities(instance: string): Promise<Capabilities> {
    try {
      const value = await request<Capabilities>('/api/v1/capabilities', instance)
      return { ...value, source: 'rfc' }
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return legacyCapabilities(instance)
      throw error
    }
  },

  async overview(instance: string): Promise<Overview> {
    try {
      const value = await request<Overview>(instancePath(instance, '/overview'), instance)
      return { ...value, source: 'rfc' }
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 404) throw error
      const [health, activity] = await Promise.all([
        request<{
          status: string
          subscriber: string
          activeTurns?: number
          connectors?: Record<string, { status?: string; running?: boolean; error?: string }>
        }>('/health', instance),
        request<LegacyActivity>('/api/activity', instance, { limit: 1 }),
      ])
      const connectors = Object.entries(health.connectors ?? {}).map(([id, value]) => ({
        id,
        state: value.running === false || value.status === 'stopped' ? 'stopped' : value.status ?? 'running',
        ready: value.running !== false && value.status !== 'stopped',
        message: value.error,
      }))
      return {
        instanceId: instance,
        health: health.status,
        ready: activity.service.ready,
        connectors,
        runtime: { runtime: 'Unavailable in legacy API', work: { activeTurns: activity.service.activeTurns } },
        outbox: { uncertain: activity.overview.attention },
        generatedAt: new Date(activity.generatedAt).toISOString(),
        source: 'legacy',
      }
    }
  },

  tasks: (instance: string, query: Query) =>
    request<Page<Task>>(instancePath(instance, '/tasks'), instance, query),
  task: (instance: string, taskId: string) =>
    request<Task>(instancePath(instance, `/tasks/${encodeURIComponent(taskId)}`), instance),
  taskInputs: (instance: string, taskId: string, query: Query = {}) =>
    request<Page<TaskInput>>(instancePath(instance, `/tasks/${encodeURIComponent(taskId)}/inputs`), instance, query),
  taskReports: (instance: string, taskId: string, query: Query = {}) =>
    request<Page<TaskReport>>(instancePath(instance, `/tasks/${encodeURIComponent(taskId)}/reports`), instance, query),
  taskEvents: (instance: string, taskId: string, query: Query = {}) =>
    request<Page<Activity>>(instancePath(instance, `/tasks/${encodeURIComponent(taskId)}/events`), instance, query),
  sessions: async (instance: string): Promise<Page<Session>> => {
    try {
      return await request<Page<Session>>(instancePath(instance, '/sessions'), instance)
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 404) throw error
      const legacy = await request<LegacyActivity>('/api/activity', instance, { limit: 1 })
      return {
        items: legacy.sessions.map((session, index) => ({
          epoch: session.epoch,
          startedAt: session.startMs ? new Date(session.startMs).toISOString() : null,
          endedAt: session.endMs ? new Date(session.endMs).toISOString() : null,
          activityCount: session.activityCount,
          current: index === legacy.sessions.length - 1,
        })),
        nextCursor: null,
      }
    }
  },
  activities: async (instance: string, query: Query = {}) => {
    try {
      return await request<Page<Activity>>(instancePath(instance, '/activities'), instance, query)
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 404) throw error
      const legacyQuery: Query = {
        limit: query.limit, before: query.before, after: query.after, epoch: query.epoch,
      }
      const legacy = await request<LegacyActivity>('/api/activity', instance, legacyQuery)
      const page = legacyActivity(legacy)
      const kind = typeof query.kind === 'string' ? query.kind : ''
      if (kind) page.items = page.items.filter((item) => item.kind.startsWith(`${kind}.`))
      return page
    }
  },
  metrics: (instance: string) => request<Metrics>(instancePath(instance, '/metrics'), instance, { window: '1h' }),
}
