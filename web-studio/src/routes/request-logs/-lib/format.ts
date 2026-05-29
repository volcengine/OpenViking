import type { RequestLogStatus } from '../-types/audit'

export function normalizeStatus(statusCode?: number): RequestLogStatus {
  return statusCode !== undefined && statusCode >= 200 && statusCode < 400
    ? 'success'
    : 'error'
}

export function formatTime(value?: string): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  const h24 = date.getHours()
  const period = h24 < 12 ? 'am' : 'pm'
  const h12 = h24 % 12 === 0 ? 12 : h24 % 12
  return `${date.getFullYear()}.${pad(date.getMonth() + 1)}.${pad(date.getDate())} ${pad(h12)}:${pad(date.getMinutes())}:${pad(date.getSeconds())}${period}`
}

export function formatDuration(value?: number): string {
  if (value === undefined) {
    return '-'
  }

  if (value < 1000) {
    return `${Math.round(value)} ms`
  }

  return `${(value / 1000).toFixed(2)} s`
}

export function formatPercent(value?: number): string {
  if (value === undefined) return '-'
  return `${Math.round(value * 100)}%`
}

export function getStatusTone(
  status: RequestLogStatus,
  statusCode?: number,
): string {
  if (status === 'error' || (statusCode && statusCode >= 400)) {
    return 'border-destructive/20 bg-destructive/10 text-destructive'
  }

  return 'border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
}

export function methodTone(method: string): string {
  switch (method) {
    case 'GET':
      return 'text-sky-700 dark:text-sky-300'
    case 'POST':
      return 'text-emerald-700 dark:text-emerald-300'
    case 'PUT':
    case 'PATCH':
      return 'text-amber-700 dark:text-amber-300'
    case 'DELETE':
      return 'text-destructive'
    default:
      return 'text-muted-foreground'
  }
}
