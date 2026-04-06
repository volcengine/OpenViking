import { OvClientError } from '#/lib/ov-client'

export type FsEntry = {
  abstract: string
  isDir: boolean
  modTime: string
  size: string
  uri: string
}

export type FindRow = Record<string, unknown>

export type LatestResult = {
  title: string
  value: unknown
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export function pickFirstNonEmpty(values: Array<unknown>): unknown {
  for (const value of values) {
    if (value !== undefined && value !== null && String(value).trim() !== '') {
      return value
    }
  }
  return ''
}

export function formatResult(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }
  return JSON.stringify(value, null, 2)
}

export function getErrorMessage(error: unknown): string {
  if (error instanceof OvClientError) {
    return `${error.code}: ${error.message}`
  }
  if (error instanceof Error) {
    return error.message
  }
  return String(error)
}

// --- URI helpers ---

export function normalizeDirUri(uri: string): string {
  const value = uri.trim()
  if (!value) {
    return 'viking://'
  }
  if (value === 'viking://') {
    return value
  }
  return value.endsWith('/') ? value : `${value}/`
}

export function parentUri(uri: string): string {
  const normalized = normalizeDirUri(uri)
  if (normalized === 'viking://') {
    return normalized
  }

  const body = normalized.slice('viking://'.length, -1)
  if (!body.includes('/')) {
    return 'viking://'
  }

  return `viking://${body.slice(0, body.lastIndexOf('/') + 1)}`
}

export function joinUri(baseUri: string, child: string): string {
  const raw = child.trim()
  if (!raw) {
    return normalizeDirUri(baseUri)
  }
  if (raw.startsWith('viking://')) {
    return raw
  }
  return `${normalizeDirUri(baseUri)}${raw.replace(/^\//, '')}`
}

// --- Normalize helpers ---

export function normalizeFsEntries(result: unknown, currentUri: string): Array<FsEntry> {
  const toEntry = (item: unknown): FsEntry => {
    if (typeof item === 'string') {
      const isDir = item.endsWith('/')
      const uri = joinUri(currentUri, item)
      return {
        abstract: '',
        isDir,
        modTime: '',
        size: '',
        uri: isDir ? normalizeDirUri(uri) : uri,
      }
    }

    if (isRecord(item)) {
      const label = String(
        pickFirstNonEmpty([item.name, item.path, item.relative_path, item.uri, item.id, 'unknown']),
      )
      const isDir =
        Boolean(item.is_dir) ||
        Boolean(item.isDir) ||
        item.type === 'dir' ||
        item.type === 'directory' ||
        label.endsWith('/')

      const uri = String(pickFirstNonEmpty([item.uri, item.path, item.relative_path, label]))
      return {
        abstract: String(pickFirstNonEmpty([item.abstract, item.summary, item.description])),
        isDir,
        modTime: String(
          pickFirstNonEmpty([
            item.modTime,
            item.mod_time,
            item.modified_at,
            item.modifiedAt,
            item.updated_at,
            item.updatedAt,
          ]),
        ),
        size: String(
          pickFirstNonEmpty([item.size, item.size_bytes, item.content_length, item.contentLength]),
        ),
        uri: isDir ? normalizeDirUri(joinUri(currentUri, uri)) : joinUri(currentUri, uri),
      }
    }

    return {
      abstract: '',
      isDir: false,
      modTime: '',
      size: '',
      uri: String(item),
    }
  }

  if (Array.isArray(result)) {
    return result.map(toEntry)
  }

  if (isRecord(result)) {
    const buckets = [result.entries, result.items, result.children, result.results]
    for (const bucket of buckets) {
      if (Array.isArray(bucket)) {
        return bucket.map(toEntry)
      }
    }
  }

  return []
}

export function normalizeReadContent(result: unknown): string {
  if (typeof result === 'string') {
    return result
  }
  if (Array.isArray(result)) {
    return result.map((item) => String(item)).join('\n')
  }
  if (isRecord(result)) {
    const content = pickFirstNonEmpty([result.content, result.text, result.body, result.value, result.data])
    if (typeof content === 'string') {
      return content
    }
  }
  return JSON.stringify(result, null, 2)
}

export function extractDeepestObjectArray(value: unknown): Array<Record<string, unknown>> | null {
  let bestDepth = -1
  let best: Array<Record<string, unknown>> | null = null

  const visit = (candidate: unknown, depth: number) => {
    if (Array.isArray(candidate)) {
      if (candidate.length > 0 && candidate.every((item) => isRecord(item)) && depth > bestDepth) {
        bestDepth = depth
        best = candidate as Array<Record<string, unknown>>
      }

      for (const item of candidate) {
        visit(item, depth + 1)
      }
      return
    }

    if (!isRecord(candidate)) {
      return
    }

    for (const nested of Object.values(candidate)) {
      visit(nested, depth + 1)
    }
  }

  visit(value, 0)
  return best
}

export function normalizeFindRows(result: unknown): Array<FindRow> {
  if (Array.isArray(result)) {
    return result.map((item) => (isRecord(item) ? item : { value: item }))
  }

  if (isRecord(result)) {
    const topLevelArrays = [result.results, result.items, result.matches, result.hits, result.rows, result.entries]
    for (const value of topLevelArrays) {
      if (Array.isArray(value)) {
        return value.map((item) => (isRecord(item) ? item : { value: item }))
      }
    }

    const deepRows = extractDeepestObjectArray(result)
    if (deepRows) {
      return deepRows
    }

    return [result]
  }

  if (result === null || result === undefined) {
    return []
  }

  return [{ value: result }]
}

export function collectFindColumns(rows: Array<FindRow>): Array<string> {
  const columns: Array<string> = []
  const seen = new Set<string>()

  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!seen.has(key)) {
        seen.add(key)
        columns.push(key)
      }
    }
  }

  return columns.length ? columns : ['value']
}
