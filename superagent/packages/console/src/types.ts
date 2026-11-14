export type ModuleState = {
  enabled: boolean
  available: boolean
  effective: boolean
  reasonCode: string | null
  message?: string
}

export type Capabilities = {
  instanceId: string
  apiVersion?: string
  modules: Record<string, ModuleState>
  generatedAt?: string
  source?: 'rfc' | 'legacy'
}

export type Instance = {
  id: string
  label: string
  default?: boolean
  process?: { state?: string; pid?: number | null }
  ready?: boolean | null
  health?: string
}

export type InstanceList = {
  items: Instance[]
  defaultInstance: string
  generatedAt?: string
}

export type Connector = {
  id: string
  kind?: string
  state: string
  ready?: boolean
  message?: string
  repairHint?: string
}

export type Overview = {
  instanceId: string
  health: 'healthy' | 'degraded' | 'unhealthy' | 'offline' | string
  ready: boolean
  connectors: Connector[]
  runtime?: Runtime | null
  tasks?: Record<string, number>
  workers?: { active?: number; limit?: number; queued?: number; oldestWaitMs?: number | null }
  alerts?: Array<{ severity: string; code: string; message: string; repairHint?: string }>
  outbox?: { pending?: number; uncertain?: number }
  generatedAt?: string
  source?: 'rfc' | 'legacy'
}

export type Runtime = {
  runtime: string
  model?: string | null
  effort?: string | null
  revision?: number
  epoch?: number | null
  work?: { state?: string; activeTurns?: number }
  capabilities?: Record<string, boolean>
}

export type Task = {
  id: string
  revision: number
  title: string
  summary?: string
  status: string
  runtime: string
  origin?: { source?: string; sourceRef?: string; messageRef?: string }
  turns: number
  recovering?: boolean
  worker?: {
    state?: string
    turn?: number
    startedAt?: string
    sessionRef?: string
    canCancel?: boolean
    stopRequested?: boolean
    termination?: string
  }
  usage?: Usage
  createdAt: string
  updatedAt: string
  errorKind?: string
}

export type Usage = {
  inputTokens?: number
  cachedInputTokens?: number
  outputTokens?: number
  cost?: number
  currency?: string
}

export type Page<T> = {
  items: T[]
  nextCursor: string | null
  hasNewer?: boolean
  generatedAt?: string
}

export type TaskInput = {
  id: string
  text?: string
  state?: string
  claimedAt?: string | null
  deliveredAt?: string | null
  createdAt: string
}

export type TaskReport = {
  id: string
  state: string
  summary?: string
  draft?: string
  question?: string
  artifacts?: Array<{ label: string; url?: string }>
  relay?: { state?: string; inboxId?: string | null; at?: string | null }
  finalDelivery?: { outboxId?: string | null; status?: string | null }
  createdAt: string
}

export type Activity = {
  id: string
  kind: string
  category?: string
  severity?: string
  summary?: string
  taskId?: string
  turn?: number
  createdAt: string
}

export type Session = {
  epoch: number
  startedAt?: string | null
  endedAt?: string | null
  journalCount?: number
  activityCount?: number
  current?: boolean
}

export type Metrics = {
  window: string
  tasks?: Record<string, number>
  queue?: { oldestWaitMs?: number | null; averageWaitMs?: number | null }
  turns?: { succeeded?: number; retried?: number; recovered?: number; failed?: number }
  usage?: Usage & { costCoverage?: 'available' | 'tokens_only' | 'unavailable' }
  inbox?: { pending?: number }
  outbox?: { uncertain?: number }
  generatedAt?: string
}
