import { parse as parseYaml } from 'yaml'

import { isDirectorySemanticSidecarUri } from './viking-uri'

export interface OkfSourceMetadata {
  kind: string
  uri: string
}

export interface OkfGeneratedByMetadata {
  component: string
  trigger: string
}

export interface OkfFreshnessMetadata {
  pending_child_changes: number
  sampled_entries: number
  total_entries: number
  unsampled_entries: number
}

export interface OkfSidecarMetadata {
  directory: string
  freshness?: OkfFreshnessMetadata
  generated_by?: OkfGeneratedByMetadata
  source?: OkfSourceMetadata
}

export interface OkfSidecarDocument {
  body: string
  metadata: OkfSidecarMetadata
  rawFrontmatter: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function nonNegativeInteger(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
    ? value
    : null
}

function parseSource(value: unknown): OkfSourceMetadata | undefined | null {
  if (value === undefined) return undefined
  if (!isRecord(value)) return null
  const kind = nonEmptyString(value.kind)
  const uri = nonEmptyString(value.uri)
  return kind && uri ? { kind, uri } : null
}

function parseGeneratedBy(
  value: unknown,
): OkfGeneratedByMetadata | undefined | null {
  if (value === undefined) return undefined
  if (!isRecord(value)) return null
  const component = nonEmptyString(value.component)
  const trigger = nonEmptyString(value.trigger)
  return component && trigger ? { component, trigger } : null
}

function parseFreshness(
  value: unknown,
): OkfFreshnessMetadata | undefined | null {
  if (value === undefined) return undefined
  if (!isRecord(value)) return null

  const totalEntries = nonNegativeInteger(value.total_entries)
  const sampledEntries = nonNegativeInteger(value.sampled_entries)
  const unsampledEntries = nonNegativeInteger(value.unsampled_entries)
  const pendingChildChanges = nonNegativeInteger(value.pending_child_changes)
  if (
    totalEntries === null ||
    sampledEntries === null ||
    unsampledEntries === null ||
    pendingChildChanges === null ||
    sampledEntries + unsampledEntries !== totalEntries
  ) {
    return null
  }

  return {
    pending_child_changes: pendingChildChanges,
    sampled_entries: sampledEntries,
    total_entries: totalEntries,
    unsampled_entries: unsampledEntries,
  }
}

function normalizeMetadata(value: unknown): OkfSidecarMetadata | null {
  if (!isRecord(value)) return null
  const directory = nonEmptyString(value.directory)
  if (!directory?.startsWith('viking://')) return null

  const source = parseSource(value.source)
  const generatedBy = parseGeneratedBy(value.generated_by)
  const freshness = parseFreshness(value.freshness)
  if (source === null || generatedBy === null || freshness === null) return null

  return {
    directory,
    ...(source ? { source } : {}),
    ...(generatedBy ? { generated_by: generatedBy } : {}),
    ...(freshness ? { freshness } : {}),
  }
}

/**
 * Parse the strict YAML frontmatter used by generated L0/L1 sidecars.
 * Ordinary Markdown and malformed/legacy sidecars return null so callers can
 * render their original content without accidentally hiding user text.
 */
export function parseOkfSidecarMarkdown(
  uri: string,
  content: string,
): OkfSidecarDocument | null {
  if (!isDirectorySemanticSidecarUri(uri)) return null

  const normalized = content.replace(/\r\n?/g, '\n')
  const lines = normalized.split('\n')
  if (lines[0] !== '---') return null

  const closingIndex = lines.indexOf('---', 1)
  if (closingIndex < 0) return null

  let parsed: unknown
  try {
    parsed = parseYaml(lines.slice(1, closingIndex).join('\n'))
  } catch {
    return null
  }

  const metadata = normalizeMetadata(parsed)
  if (!metadata) return null

  const bodyLines = lines.slice(closingIndex + 1)
  if (bodyLines[0] === '') bodyLines.shift()
  return {
    body: bodyLines.join('\n'),
    metadata,
    rawFrontmatter: lines.slice(0, closingIndex + 1).join('\n'),
  }
}
