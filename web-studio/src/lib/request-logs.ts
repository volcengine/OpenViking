export type RequestLogStatus = 'pending' | 'success' | 'error'

export type RequestLogEntry = {
  id: string
  durationMs?: number
  errorMessage?: string
  method: string
  path: string
  requestId?: string
  startedAt: string
  status: RequestLogStatus
  statusCode?: number
}

type RequestLogListener = () => void

const MAX_REQUEST_LOGS = 300

let entries: RequestLogEntry[] = []
const listeners = new Set<RequestLogListener>()

function emitChange(): void {
  for (const listener of listeners) {
    listener()
  }
}

export function subscribeRequestLogs(listener: RequestLogListener): () => void {
  listeners.add(listener)

  return () => {
    listeners.delete(listener)
  }
}

export function getRequestLogSnapshot(): readonly RequestLogEntry[] {
  return entries
}

export function clearRequestLogs(): void {
  entries = []
  emitChange()
}

export function addRequestLog(entry: RequestLogEntry): void {
  entries = [entry, ...entries].slice(0, MAX_REQUEST_LOGS)
  emitChange()
}

export function updateRequestLog(id: string, patch: Partial<RequestLogEntry>): void {
  entries = entries.map((entry) => (entry.id === id ? { ...entry, ...patch } : entry))
  emitChange()
}
