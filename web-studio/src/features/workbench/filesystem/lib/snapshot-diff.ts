export interface DiffRow {
  oldLine?: number
  newLine?: number
  oldText?: string
  newText?: string
  oldNoNewline?: true
  newNoNewline?: true
  kind: 'context' | 'added' | 'removed' | 'changed'
}

export interface DiffHunk {
  label: string
  rows: DiffRow[]
}

interface DiffLine {
  text: string
  noNewline?: true
}

export function parseSnapshotDiff(diff: string): DiffHunk[] {
  const hunks: DiffHunk[] = []
  let hunk: DiffHunk | null = null
  let oldLine = 0
  let newLine = 0
  let removed: DiffLine[] = []
  let added: DiffLine[] = []
  let previousPrefix: string | undefined

  function flushChanges() {
    if (!hunk) return
    const count = Math.max(removed.length, added.length)
    for (let index = 0; index < count; index++) {
      const old = removed[index]
      const next = added[index]
      const row: DiffRow = {
        ...(old ? { oldLine: oldLine++, oldText: old.text } : {}),
        ...(next ? { newLine: newLine++, newText: next.text } : {}),
        kind: !old ? 'added' : !next ? 'removed' : 'changed',
      }
      if (old?.noNewline) row.oldNoNewline = true
      if (next?.noNewline) row.newNoNewline = true
      hunk.rows.push(row)
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
      removed.push({ text: line.slice(1) })
    } else if (hunk && line.startsWith('+')) {
      added.push({ text: line.slice(1) })
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
      if (previousPrefix === '-') {
        const previous = removed.at(-1)
        if (previous) previous.noNewline = true
      } else if (previousPrefix === '+') {
        const previous = added.at(-1)
        if (previous) previous.noNewline = true
      } else if (previousPrefix === ' ') {
        const previous = hunk.rows.at(-1)
        if (previous?.kind === 'context') {
          previous.oldNoNewline = true
          previous.newNoNewline = true
        }
      }
    }
    previousPrefix = line[0]
  }
  flushChanges()
  return hunks
}
