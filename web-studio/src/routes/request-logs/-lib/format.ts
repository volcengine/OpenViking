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
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(date)
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
