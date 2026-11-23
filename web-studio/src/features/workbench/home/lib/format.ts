import i18n from '#/i18n/core'

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
  return asNumber(value).toLocaleString(i18n.resolvedLanguage ?? i18n.language)
}

export function parseDateKey(value: string | undefined): Date {
  const fallback = new Date()
  if (!value) return fallback
  const [year, month, day] = value.split('-').map(Number)
  if (!year || !month || !day) return fallback
  return new Date(year, month - 1, day)
}

export function getViewerTimezone(): string {
  // Falls back to UTC when Intl is unavailable or returns an empty string
  // (e.g. very old Safari, jest jsdom edge cases).
  try {
    // eslint-disable-next-line studio-i18n/explicit-locale -- Read the browser timezone; no date is displayed.
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

function formatDateInTimezone(d: Date, timeZone: string): string {
  // `en-CA` produces YYYY-MM-DD, which matches the backend `date` query
  // parameter shape exactly.
  return new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(d)
}

export function getLastDaysRange(
  days: number,
  timeZone?: string,
): {
  endDate: string
  startDate: string
} {
  const tz = timeZone ?? getViewerTimezone()
  const endDate = formatDateInTimezone(new Date(), tz)
  // Use UTC only for calendar arithmetic on the viewer's date, not elapsed time.
  const earlier = new Date(`${endDate}T00:00:00Z`)
  earlier.setUTCDate(earlier.getUTCDate() - (days - 1))
  return {
    endDate,
    startDate: earlier.toISOString().slice(0, 10),
  }
}

export function formatShortDate(value: string): string {
  if (!value) return '--'
  const date = new Date(`${value}T00:00:00`)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString(i18n.resolvedLanguage ?? i18n.language, {
    day: '2-digit',
    month: '2-digit',
  })
}

export function isDisabledPayload(value: unknown): boolean {
  return asRecord(value).enabled === false
}
