export const dateTime = (value?: string | null) => {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(date)
}

export const relativeTime = (value?: string | null) => {
  if (!value) return '—'
  const delta = Date.now() - new Date(value).getTime()
  if (!Number.isFinite(delta)) return value
  const minutes = Math.round(delta / 60_000)
  if (Math.abs(minutes) < 1) return 'just now'
  if (Math.abs(minutes) < 60) return `${Math.abs(minutes)}m ${minutes > 0 ? 'ago' : 'from now'}`
  const hours = Math.round(minutes / 60)
  if (Math.abs(hours) < 24) return `${Math.abs(hours)}h ${hours > 0 ? 'ago' : 'from now'}`
  return dateTime(value)
}

export const number = (value?: number | null) => value === undefined || value === null ? '—' : new Intl.NumberFormat().format(value)
export const tokens = (value?: number | null) => value === undefined || value === null ? '—' : value >= 1000 ? `${(value / 1000).toFixed(value >= 10_000 ? 0 : 1)}k` : String(value)
export const duration = (ms?: number | null) => {
  if (ms === undefined || ms === null) return '—'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`
}
export const titleCase = (value?: string | null) => (value ?? 'unknown').replaceAll('_', ' ').replaceAll('-', ' ')

export const reasonText = (code?: string | null) => ({
  disabled_by_owner: 'Disabled by owner',
  workers_not_merged: 'Workers backend not available',
  scheduler_not_workers: 'Instance is not using the workers scheduler',
  runtime_unsupported: 'Runtime does not support this module',
  platform_unsupported: 'Not supported on this platform',
  control_broker_unavailable: 'Local Control Broker unavailable',
  authentication_required: 'Owner authentication required',
  permission_denied: 'Insufficient permission',
  dependency_disabled: 'A required module is disabled',
  instance_offline: 'Instance is offline',
  audit_store_unavailable: 'Audit store not available',
  not_provided: 'Intentionally not provided',
}[code ?? ''] ?? titleCase(code))
