export type MemoryDiffKind = 'add' | 'delete' | 'update'

export type SessionMemoryDiffOperation = {
  after?: string
  before?: string
  kind: MemoryDiffKind
  memoryType: string
  uri: string
}

export type SessionMemoryDiff = {
  archiveId: string
  archiveUri: string
  extractedAt: string
  operations: SessionMemoryDiffOperation[]
  summary: {
    adds: number
    deletes: number
    updates: number
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function parseOperation(
  value: unknown,
  kind: MemoryDiffKind,
): SessionMemoryDiffOperation | null {
  if (!isRecord(value)) return null
  const uri = readString(value.uri)
  if (!uri) return null

  return {
    after:
      kind === 'delete'
        ? readString(value.deleted_content)
        : readString(value.after),
    before: kind === 'update' ? readString(value.before) : undefined,
    kind,
    memoryType: readString(value.memory_type) || 'unknown',
    uri,
  }
}

function parseOperationList(
  value: unknown,
  kind: MemoryDiffKind,
): SessionMemoryDiffOperation[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) => {
    const operation = parseOperation(item, kind)
    return operation ? [operation] : []
  })
}

export function parseSessionMemoryDiff(
  value: unknown,
  archiveId: string,
): SessionMemoryDiff | null {
  let parsed = value
  if (typeof value === 'string') {
    try {
      parsed = JSON.parse(value) as unknown
    } catch {
      return null
    }
  }
  if (!isRecord(parsed)) return null

  const rawOperations = isRecord(parsed.operations) ? parsed.operations : {}
  const adds = parseOperationList(rawOperations.adds, 'add')
  const updates = parseOperationList(rawOperations.updates, 'update')
  const deletes = parseOperationList(rawOperations.deletes, 'delete')
  const operations = [...adds, ...updates, ...deletes]
  if (operations.length === 0) return null

  return {
    archiveId,
    archiveUri: readString(parsed.archive_uri),
    extractedAt: readString(parsed.extracted_at),
    operations,
    summary: {
      adds: adds.length,
      deletes: deletes.length,
      updates: updates.length,
    },
  }
}
