import type { VikingFileType, VikingFsEntry } from '../-types/viking-fm'

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

const SIZE_REGEX = /^([\d.]+)\s*([kmgtp]?i?b?)$/

const SIZE_MULTIPLIERS: Record<string, number> = {
  b: 1,
  kb: 1024,
  kib: 1024,
  mb: 1024 ** 2,
  mib: 1024 ** 2,
  gb: 1024 ** 3,
  gib: 1024 ** 3,
  tb: 1024 ** 4,
  tib: 1024 ** 4,
  pb: 1024 ** 5,
  pib: 1024 ** 5,
}

const IMAGE_EXTS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'ico', 'bmp', 'avif']
const MARKDOWN_EXTS = ['md', 'markdown', 'mdx']
const CODE_EXTS = [
  'js', 'ts', 'jsx', 'tsx', 'mjs', 'cjs',
  'py', 'go', 'rs', 'java', 'kt', 'c', 'h', 'hpp', 'cpp',
  'json', 'yaml', 'yml', 'toml', 'xml',
  'html', 'css', 'scss', 'less',
  'sh', 'bash', 'zsh', 'fish', 'sql', 'graphql', 'proto',
]
const BINARY_EXTS = [
  'pdf', 'zip', 'gz', 'tgz', 'tar', '7z', 'rar',
  'mp3', 'wav', 'mp4', 'mov', 'avi', 'mkv',
  'woff', 'woff2', 'ttf', 'otf',
  'exe', 'dll', 'so', 'dylib', 'bin',
]
const TEXT_FILES = ['readme', 'license', 'dockerfile', 'makefile']

function pickFirstNonEmpty(values: Array<unknown>): unknown {
  for (const value of values) {
    if (value !== undefined && value !== null && String(value).trim() !== '') {
      return value
    }
  }
  return ''
}

function fileNameFromUri(uri: string): string {
  const trimmed = uri.endsWith('/') ? uri.slice(0, -1) : uri
  const index = trimmed.lastIndexOf('/')
  if (index < 0) return trimmed
  return trimmed.slice(index + 1) || trimmed
}

export { fileNameFromUri }

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

export function normalizeFileUri(uri: string): string {
  const value = uri.trim()
  if (!value) {
    return 'viking://'
  }
  if (value === 'viking://') {
    return value
  }
  return value.endsWith('/') ? value.slice(0, -1) : value
}

export function sameUri(left: string, right: string): boolean {
  const leftNormalized = left.endsWith('/') || left === 'viking://'
    ? normalizeDirUri(left)
    : normalizeFileUri(left)
  const rightNormalized = right.endsWith('/') || right === 'viking://'
    ? normalizeDirUri(right)
    : normalizeFileUri(right)

  return leftNormalized === rightNormalized
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

  const normalizedBase = normalizeDirUri(baseUri)
  return `${normalizedBase}${raw.replace(/^\//, '')}`
}

export function parseSizeToBytes(value: unknown): number | null {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null
  }

  const text = String(value ?? '').trim()
  if (!text) {
    return null
  }

  if (/^\d+$/.test(text)) {
    const direct = Number(text)
    return Number.isFinite(direct) ? direct : null
  }

  const normalized = text.replace(/,/g, '').toLowerCase()
  const match = normalized.match(SIZE_REGEX)
  if (!match) {
    return null
  }

  const amount = Number(match[1])
  const unit = match[2]
  if (!Number.isFinite(amount)) {
    return null
  }

  const multiplier = SIZE_MULTIPLIERS[unit || 'b']
  if (!multiplier) {
    return null
  }

  return Math.round(amount * multiplier)
}

export function parseModTimeToTs(value: unknown): number | null {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null
  }

  const text = String(value ?? '').trim()
  if (!text) {
    return null
  }

  const direct = Date.parse(text)
  if (Number.isFinite(direct)) {
    return direct
  }

  const fallback = Date.parse(text.replace(' ', 'T'))
  if (Number.isFinite(fallback)) {
    return fallback
  }

  return null
}

export function detectFileType(uri: string): VikingFileType {
  const name = fileNameFromUri(uri).toLowerCase()
  const ext = name.includes('.') ? name.split('.').pop() || '' : ''

  if (IMAGE_EXTS.includes(ext)) {
    return 'image'
  }
  if (MARKDOWN_EXTS.includes(ext)) {
    return 'markdown'
  }
  if (CODE_EXTS.includes(ext)) {
    return 'code'
  }

  if (!ext && TEXT_FILES.includes(name)) {
    return 'text'
  }

  if (isLikelyBinary(uri)) {
    return 'binary'
  }

  return 'text'
}

export function isLikelyBinary(uri: string): boolean {
  const name = fileNameFromUri(uri).toLowerCase()
  const ext = name.includes('.') ? name.split('.').pop() || '' : ''

  return BINARY_EXTS.includes(ext)
}

export function shouldAutoRead(entry: Pick<VikingFsEntry, 'isDir' | 'uri' | 'sizeBytes'>, maxAutoReadBytes = 2 * 1024 * 1024): {
  shouldRead: boolean
  reason?: 'binary' | 'too-large'
} {
  if (entry.isDir) {
    return { shouldRead: false }
  }

  const fileType = detectFileType(entry.uri)
  if (fileType === 'image' || isLikelyBinary(entry.uri)) {
    return { shouldRead: false, reason: 'binary' }
  }

  if (entry.sizeBytes !== null && entry.sizeBytes > maxAutoReadBytes) {
    return { shouldRead: false, reason: 'too-large' }
  }

  return { shouldRead: true }
}

export function normalizeFsEntry(item: unknown, currentUri: string): VikingFsEntry {
  if (typeof item === 'string') {
    const isDir = item.endsWith('/')
    const uri = joinUri(currentUri, item)
    const normalizedUri = isDir ? normalizeDirUri(uri) : normalizeFileUri(uri)

    return {
      uri: normalizedUri,
      name: fileNameFromUri(normalizedUri),
      isDir,
      size: '',
      sizeBytes: null,
      modTime: '',
      modTimestamp: null,
      abstract: '',
      tags: undefined,
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

    const rawUri = String(pickFirstNonEmpty([item.uri, item.path, item.relative_path, label]))
    const joinedUri = joinUri(currentUri, rawUri)
    const normalizedUri = isDir ? normalizeDirUri(joinedUri) : normalizeFileUri(joinedUri)

    const sizeRaw = pickFirstNonEmpty([item.size, item.size_bytes, item.content_length, item.contentLength])
    const modRaw = pickFirstNonEmpty([
      item.modTime,
      item.mod_time,
      item.modified_at,
      item.modifiedAt,
      item.updated_at,
      item.updatedAt,
    ])

    return {
      uri: normalizedUri,
      name: String(pickFirstNonEmpty([item.name, fileNameFromUri(normalizedUri)])),
      isDir,
      size: String(sizeRaw ?? ''),
      sizeBytes: parseSizeToBytes(sizeRaw),
      modTime: String(modRaw ?? ''),
      modTimestamp: parseModTimeToTs(modRaw),
      abstract: String(pickFirstNonEmpty([item.abstract, item.summary, item.description])),
      tags: String(pickFirstNonEmpty([item.tags, item.tag, ''])) || undefined,
    }
  }

  const fallbackUri = normalizeFileUri(joinUri(currentUri, String(item ?? '')))
  return {
    uri: fallbackUri,
    name: fileNameFromUri(fallbackUri),
    isDir: false,
    size: '',
    sizeBytes: null,
    modTime: '',
    modTimestamp: null,
    abstract: '',
    tags: undefined,
  }
}

export function normalizeFsEntries(result: unknown, currentUri: string): Array<VikingFsEntry> {
  const normalizedCurrentUri = normalizeDirUri(currentUri)

  if (Array.isArray(result)) {
    return result.map((item) => normalizeFsEntry(item, normalizedCurrentUri))
  }

  if (isRecord(result)) {
    const buckets = [result.entries, result.items, result.children, result.results, result.nodes]
    for (const bucket of buckets) {
      if (Array.isArray(bucket)) {
        return bucket.map((item) => normalizeFsEntry(item, normalizedCurrentUri))
      }
    }
  }

  return []
}

export function formatSize(value: unknown, options?: { maximumFractionDigits?: number; fallback?: string }): string {
  const maximumFractionDigits = options?.maximumFractionDigits ?? 1
  const fallback = options?.fallback ?? '-'
  const bytes = parseSizeToBytes(value)

  if (bytes === null || bytes < 0) {
    return fallback
  }

  if (bytes < 1024) {
    return `${bytes} B`
  }

  const units = ['KB', 'MB', 'GB', 'TB', 'PB']
  let scaled = bytes
  let unitIndex = -1

  while (scaled >= 1024 && unitIndex < units.length - 1) {
    scaled /= 1024
    unitIndex += 1
  }

  return `${scaled.toFixed(maximumFractionDigits)} ${units[unitIndex]}`
}

export function normalizeUriForDisplay(uri: string, isDir: boolean): string {
  return isDir ? normalizeDirUri(uri) : normalizeFileUri(uri)
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
