export interface DiffRow {
  oldLine?: number
  newLine?: number
  oldText?: string
  newText?: string
  kind: 'context' | 'added' | 'removed' | 'changed'
}

export interface DiffHunk {
  label: string
  rows: DiffRow[]
}

export function parseSnapshotDiff(diff: string): DiffHunk[] {
  const hunks: DiffHunk[] = []
  let hunk: DiffHunk | null = null
  let oldLine = 0
  let newLine = 0
  let removed: string[] = []
  let added: string[] = []

  function flushChanges() {
    if (!hunk) return
    const count = Math.max(removed.length, added.length)
    for (let index = 0; index < count; index++) {
      const hasOld = index < removed.length
      const hasNew = index < added.length
      hunk.rows.push({
        ...(hasOld ? { oldLine: oldLine++, oldText: removed[index] } : {}),
        ...(hasNew ? { newLine: newLine++, newText: added[index] } : {}),
        kind: !hasOld ? 'added' : !hasNew ? 'removed' : 'changed',
      })
    }
    removed = []
    added = []
  }

  for (const line of diff.split('\n')) {
    const match = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/.exec(line)
    if (match) {
      flushChanges()
      oldLine = Number(match[1])
      newLine = Number(match[2])
      hunk = { label: match[3].trim(), rows: [] }
      hunks.push(hunk)
    } else if (hunk && line.startsWith('-')) {
      removed.push(line.slice(1))
    } else if (hunk && line.startsWith('+')) {
      added.push(line.slice(1))
    } else if (hunk && line.startsWith(' ')) {
      flushChanges()
      hunk.rows.push({
        oldLine: oldLine++,
        newLine: newLine++,
        oldText: line.slice(1),
        newText: line.slice(1),
        kind: 'context',
      })
    } else if (hunk && line === '\\ No newline at end of file') {
      continue
    }
  }
  flushChanges()
  return hunks
}
