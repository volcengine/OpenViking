/** Placeholder for a missing value. */
export const EMPTY_VALUE = '—'

const RELATIVE_STEPS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ['second', 60],
  ['minute', 60],
  ['hour', 24],
  ['day', 30],
  ['month', 12],
  ['year', Number.POSITIVE_INFINITY],
]

/** "3 minutes ago" / "3 分钟前" for an epoch-seconds timestamp. */
export function formatRelativeTime(
  epochSeconds: number,
  locale?: string,
  now = Date.now(),
): string {
  const format = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
  let value = (epochSeconds * 1000 - now) / 1000
  for (const [unit, size] of RELATIVE_STEPS) {
    if (Math.abs(value) < size) return format.format(Math.round(value), unit)
    value /= size
  }
  return format.format(Math.round(value), 'year')
}

/**
 * Medium date and time for an epoch-seconds timestamp; pass `withSeconds`
 * where entries are often seconds apart, such as request-log records.
 */
export function formatDateTime(
  epochSeconds: number,
  locale?: string,
  withSeconds = false,
): string {
  return new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeStyle: withSeconds ? 'medium' : 'short',
  }).format(epochSeconds * 1000)
}

/** Wall-clock time with seconds, e.g. for "Updated 14:03:21"; takes ms. */
export function formatClockTime(epochMs: number, locale?: string): string {
  return new Intl.DateTimeFormat(locale, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(epochMs)
}

export function formatNumber(value: number, locale?: string): string {
  return new Intl.NumberFormat(locale).format(value)
}

/** Compact count for token totals: "1.2K", "3.4M" / "1.2万". */
export function formatCompact(value: number, locale?: string): string {
  return new Intl.NumberFormat(locale, {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value)
}

/** A 0–1 ratio as a percentage; one decimal below 10 %. */
export function formatPercent(ratio: number, locale?: string): string {
  return new Intl.NumberFormat(locale, {
    style: 'percent',
    maximumFractionDigits: ratio > 0 && ratio < 0.1 ? 1 : 0,
  }).format(ratio)
}

function formatUnit(
  value: number,
  unit: string,
  locale?: string,
  digits = 1,
): string {
  return new Intl.NumberFormat(locale, {
    style: 'unit',
    unit,
    unitDisplay: 'short',
    maximumFractionDigits: digits,
  }).format(value)
}

/** Milliseconds as "820 ms" below a second, else "1.4 s". */
export function formatDuration(ms: number, locale?: string): string {
  return ms < 1000
    ? formatUnit(Math.round(ms), 'millisecond', locale, 0)
    : formatUnit(ms / 1000, 'second', locale)
}

/** Bytes as kilobytes or megabytes: "64 kB", "1 MB". */
export function formatBytes(bytes: number, locale?: string): string {
  const mega = bytes >= 1024 * 1024
  return formatUnit(
    bytes / (mega ? 1024 * 1024 : 1024),
    mega ? 'megabyte' : 'kilobyte',
    locale,
  )
}

/** Seconds in the largest fitting unit: "45 s", "10 min", "1.5 hr", "2 days". */
export function humanizeSeconds(seconds: number, locale?: string): string {
  if (seconds < 60) return formatUnit(seconds, 'second', locale)
  if (seconds < 3600) return formatUnit(seconds / 60, 'minute', locale)
  if (seconds < 86400) return formatUnit(seconds / 3600, 'hour', locale)
  return formatUnit(seconds / 86400, 'day', locale)
}

/** Host (with port) of a URL, or the input when it does not parse. */
export function hostFromUrl(url: string): string {
  try {
    return new URL(url).host || url
  } catch {
    return url
  }
}

const LOOPBACK_HOSTS = new Set(['localhost', '[::1]', '0.0.0.0'])

/** True when the URL points at this machine only (localhost, 127.x, ::1). */
export function isLoopbackUrl(url: string): boolean {
  let hostname: string
  try {
    hostname = new URL(url).hostname.toLowerCase()
  } catch {
    return false
  }
  return (
    LOOPBACK_HOSTS.has(hostname) ||
    hostname.endsWith('.localhost') ||
    /^127\.\d+\.\d+\.\d+$/.test(hostname)
  )
}

/** Long ids such as session hashes, shortened to the first `keep` characters. */
export function shortenId(value: string, keep = 8): string {
  return value.length > keep + 1 ? `${value.slice(0, keep)}…` : value
}
