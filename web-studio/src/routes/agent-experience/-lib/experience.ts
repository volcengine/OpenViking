import type {
  ExperienceFileItem,
  OutcomeCount,
  OutcomeDistribution,
  TimeRange,
  TimeRangePreset,
  TrajectoryItem,
  TrajectoryOutcome,
  TrajectoryPage,
} from './types'

/** Directory (relative to the user space root) that stores experience memories. */
export const EXPERIENCES_DIRECTORY = 'memories/experiences'

export const TRAJECTORY_OUTCOMES: readonly TrajectoryOutcome[] = [
  'success',
  'failure',
  'partial',
  'unknown',
  'unfinished',
]

export const OUTCOME_COLORS: Record<TrajectoryOutcome, string> = {
  success: 'bg-emerald-500',
  failure: 'bg-rose-500',
  partial: 'bg-amber-500',
  unknown: 'bg-zinc-400',
  unfinished: 'bg-sky-500',
}

export const DEFAULT_TRAJECTORY_PAGE_SIZE = 20

/** Build the experiences directory URI for a given user id. */
export function buildExperiencesUri(userId: string): string {
  const normalized = userId.trim() || 'default'
  return `viking://user/${encodeURIComponent(normalized)}/${EXPERIENCES_DIRECTORY}`
}

/** Extract a display name from an experience URI (`exchange.md` -> `exchange`). */
export function getExperienceDisplayName(uri: string): string {
  const fileName = uri.split('/').filter(Boolean).pop() ?? uri
  return fileName.replace(/\.md$/i, '')
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readString(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : undefined
}

export function normalizeExperienceFiles(value: unknown): ExperienceFileItem[] {
  if (!Array.isArray(value)) return []

  return value.flatMap((raw) => {
    const entry = isRecord(raw) ? raw : null
    if (!entry || entry.isDir === true) return []
    const name = readString(entry.name)
    const uri = readString(entry.uri)
    if (!name || !uri) return []
    return [
      {
        name,
        uri,
        modTime: readString(entry.modTime),
        size: typeof entry.size === 'number' ? entry.size : undefined,
      } satisfies ExperienceFileItem,
    ]
  })
}

/** Normalize `GET /api/v1/agent-evolution/experiences/trajectories` result. */
export function normalizeTrajectoryPage(
  value: unknown,
  experienceUri: string,
): TrajectoryPage | null {
  const result = isRecord(value) ? value : null
  if (!result) return null

  const rawItems = Array.isArray(result.items) ? result.items : []
  const items = rawItems.flatMap((raw) => {
    const item = isRecord(raw) ? raw : null
    const uri = item ? readString(item.uri) : undefined
    if (!item || !uri) return []

    return [
      {
        uri,
        name: readString(item.name) ?? uri.split('/').pop() ?? uri,
        description: readString(item.description),
        created_at: readString(item.created_at),
        updated_at: readString(item.updated_at),
      } satisfies TrajectoryItem,
    ]
  })

  const total = typeof result.total === 'number' ? result.total : items.length
  const limit = typeof result.limit === 'number' ? result.limit : items.length
  const offset = typeof result.offset === 'number' ? result.offset : 0

  return {
    experienceUri: readString(result.experience_uri) ?? experienceUri,
    items,
    total,
    limit,
    offset,
    hasMore:
      typeof result.has_more === 'boolean'
        ? result.has_more
        : offset + items.length < total,
  }
}

/** Normalize `GET /api/v1/agent-evolution/experiences/outcomes` result. */
export function normalizeOutcomeDistribution(
  value: unknown,
  experienceUri: string,
): OutcomeDistribution {
  const result = isRecord(value) ? value : null
  const rawDistribution = Array.isArray(result?.outcome_distribution)
    ? result.outcome_distribution
    : []
  const byOutcome = new Map<string, number>()

  for (const raw of rawDistribution) {
    const entry = isRecord(raw) ? raw : null
    const outcome = entry ? readString(entry.outcome) : undefined
    if (!entry || !outcome) continue
    byOutcome.set(outcome, Number(entry.count) || 0)
  }

  // The server always returns the five fixed buckets; fall back to zero for
  // missing entries so the UI renders a stable legend.
  const distribution: OutcomeCount[] = TRAJECTORY_OUTCOMES.map((outcome) => ({
    outcome,
    count: byOutcome.get(outcome) ?? 0,
  }))

  return {
    experienceUri: readString(result?.experience_uri) ?? experienceUri,
    distribution,
  }
}

function toUtcDate(date: Date): string {
  return date.toISOString().slice(0, 10)
}

/**
 * Resolve a quick time-range preset into UTC `YYYY-MM-DD` bounds.
 *
 * The Agent Evolution API filters trajectories by their UTC creation date, so
 * presets are computed in UTC as well. `all` disables filtering; `custom` is
 * built by {@link buildCustomTimeRange} and returns no bounds here.
 */
export function resolveTimeRange(
  preset: TimeRangePreset,
  now: Date = new Date(),
): TimeRange {
  if (preset === 'all' || preset === 'custom') {
    return { preset }
  }

  const days = preset === '7d' ? 7 : 30
  const end = new Date(now)
  const start = new Date(now)
  start.setUTCDate(start.getUTCDate() - (days - 1))

  return {
    preset,
    startDate: toUtcDate(start),
    endDate: toUtcDate(end),
  }
}

/** Format an ISO-ish timestamp for list display; returns `undefined` on garbage input. */
export function formatTimestamp(
  value: string | undefined,
  locale: string,
): string | undefined {
  if (!value) return undefined
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return undefined

  return new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

/** Human readable file size for the experience list. */
export function formatFileSize(size: number | undefined): string | undefined {
  if (size === undefined || !Number.isFinite(size) || size < 0) return undefined
  if (size < 1024) return `${size} B`

  const units = ['KB', 'MB', 'GB']
  let value = size
  let unit = 'B'
  for (const next of units) {
    if (value < 1024) break
    value /= 1024
    unit = next
  }
  return `${value >= 10 ? Math.round(value) : Math.round(value * 10) / 10} ${unit}`
}

export type SourceTrajectoryLink = {
  uri: string
  reason?: string
}

export function normalizeSourceTrajectoryLinks(
  value: unknown,
): SourceTrajectoryLink[] {
  const result = isRecord(value) ? value : null
  const attrs = result && isRecord(result.attrs) ? result.attrs : null
  const memory = attrs && isRecord(attrs.memory) ? attrs.memory : null
  const links = memory && Array.isArray(memory.links) ? memory.links : []
  const seen = new Set<string>()

  return links.flatMap((raw) => {
    const link = isRecord(raw) ? raw : null
    const uri = link ? readString(link.to_uri) : undefined
    const linkType = link ? readString(link.link_type) : undefined
    if (
      !uri ||
      linkType !== 'derived_from' ||
      !uri.includes('/memories/trajectories/') ||
      seen.has(uri)
    ) {
      return []
    }
    seen.add(uri)
    const reason = readString(link?.description) ?? readString(link?.match_text)
    return [{ uri, reason } satisfies SourceTrajectoryLink]
  })
}

/** Local-storage key tracking the last time each experience was seen. */
const EXPERIENCE_LAST_SEEN_KEY = 'ov_agent_experience_last_seen'

type LastSeenMap = Record<string, number>

function readLastSeenMap(): LastSeenMap {
  if (typeof window === 'undefined') return {}
  try {
    const raw = window.localStorage.getItem(EXPERIENCE_LAST_SEEN_KEY)
    const parsed = raw ? (JSON.parse(raw) as unknown) : {}
    return isRecord(parsed)
      ? Object.fromEntries(
          Object.entries(parsed).flatMap(([uri, value]) =>
            typeof value === 'number' ? [[uri, value] as const] : [],
          ),
        )
      : {}
  } catch {
    return {}
  }
}

function writeLastSeenMap(map: LastSeenMap): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(EXPERIENCE_LAST_SEEN_KEY, JSON.stringify(map))
  } catch {
    // Ignore storage failures (private mode, quota).
  }
}

/** Whether an experience was updated after the caller last saw it. */
export function isExperienceUpdatedSinceLastSeen(
  uri: string,
  modTime: string | undefined,
): boolean {
  if (!modTime) return false
  const updated = new Date(modTime).getTime()
  if (Number.isNaN(updated)) return false
  const map = readLastSeenMap()
  if (!Object.prototype.hasOwnProperty.call(map, uri)) return true
  return updated > map[uri]
}

/** Record "now" as the last-seen time for the given experiences. */
export function markExperiencesSeen(entries: readonly { uri: string }[]): void {
  if (entries.length === 0) return
  const map = readLastSeenMap()
  const now = Date.now()
  for (const entry of entries) {
    map[entry.uri] = now
  }
  writeLastSeenMap(map)
}

/** Parse a `YYYY-MM-DD` string; returns `undefined` when invalid. */
export function parseUtcDate(value: string): Date | undefined {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value.trim())) return undefined
  const date = new Date(`${value.trim()}T00:00:00Z`)
  return Number.isNaN(date.getTime()) ? undefined : date
}

/**
 * Build a custom time range from user-provided UTC dates.
 *
 * Returns an error key when the combination is invalid, so callers can
 * surface inline validation instead of firing a bad request.
 */
export function buildCustomTimeRange(
  startDate: string,
  endDate: string,
): { error?: 'invalid' | 'order'; range?: TimeRange } {
  const start = parseUtcDate(startDate)
  const end = parseUtcDate(endDate)
  const rawStart = startDate.trim()
  const rawEnd = endDate.trim()

  if ((rawStart && !start) || (rawEnd && !end)) return { error: 'invalid' }
  if (!rawStart && !rawEnd) return { error: 'invalid' }
  if (start && end && start > end) return { error: 'order' }

  return {
    range: {
      preset: 'all',
      startDate: rawStart || undefined,
      endDate: rawEnd || undefined,
    },
  }
}
