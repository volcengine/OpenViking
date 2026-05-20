export function asRecord(v: unknown): Record<string, unknown> {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : {}
}

export function asArray(v: unknown): unknown[] {
  return Array.isArray(v) ? v : []
}

export function asNumber(v: unknown): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0
}

export function asString(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

export function formatNumber(value: unknown): string {
  return asNumber(value).toLocaleString()
}

export function formatDateKey(date: Date): string {
  const year = date.getFullYear()
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function parseDateKey(value: string | undefined): Date {
  const fallback = new Date()
  if (!value) return fallback
  const [year, month, day] = value.split('-').map(Number)
  if (!year || !month || !day) return fallback
  return new Date(year, month - 1, day)
}

export function getLastDaysRange(days: number): {
  endDate: string
  startDate: string
} {
  const end = new Date()
  const start = new Date(end)
  start.setDate(end.getDate() - days + 1)
  return {
    endDate: formatDateKey(end),
    startDate: formatDateKey(start),
  }
}

export function formatShortDate(value: string): string {
  if (!value) return '--'
  const date = new Date(`${value}T00:00:00`)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString(undefined, {
    day: '2-digit',
    month: '2-digit',
  })
}

export function formatTimestamp(value: string): string {
  if (!value) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString(undefined, {
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    month: '2-digit',
  })
}

export function isDisabledPayload(value: unknown): boolean {
  return asRecord(value).enabled === false
}
