import type {
  ExperienceFileItem,
  OutcomeCount,
  OutcomeDistribution,
  TimeRange,
  TrajectoryItem,
  TrajectoryOutcome,
  TrajectoryPage,
} from '#/features/workbench/agent-experience/lib/types'

/** Directory (relative to the user space root) that stores experience memories. */
export const EXPERIENCES_DIRECTORY = 'memories/experiences'

export const TRAJECTORY_OUTCOMES: readonly TrajectoryOutcome[] = [
  'success',
  'failure',
  'partial',
  'unknown',
  'unfinished',
]

export const DEFAULT_TRAJECTORY_PAGE_SIZE = 20

/** Build the experiences directory URI for a given user id. */
export function buildExperiencesUri(userId: string): string {
  const normalized = userId.trim() || 'default'
  return `viking://user/${encodeURIComponent(normalized)}/${EXPERIENCES_DIRECTORY}`
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
        outcome: TRAJECTORY_OUTCOMES.find(
          (outcome) => outcome === item.outcome,
        ),
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

/** The server filters using inclusive UTC dates. Zero means all time. */
export function resolveTimeRange(days: number, now = new Date()): TimeRange {
  if (days <= 0) return {}
  const start = new Date(now)
  start.setUTCDate(start.getUTCDate() - (days - 1))
  return {
    startDate: start.toISOString().slice(0, 10),
    endDate: now.toISOString().slice(0, 10),
  }
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
