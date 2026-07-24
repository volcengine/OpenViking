export type ObserverStatusBlock =
  | {
      kind: 'table'
      headers: string[]
      rows: string[][]
    }
  | {
      kind: 'text'
      value: string
    }

function parseRow(line: string): string[] {
  return line
    .slice(1, -1)
    .split('|')
    .map((cell) => cell.trim())
}

export function parseObserverStatus(status: string): ObserverStatusBlock[] {
  const lines = status
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  const blocks: ObserverStatusBlock[] = []

  for (let index = 0; index < lines.length; ) {
    if (lines[index].startsWith('+') && lines[index + 1]?.startsWith('|')) {
      const rows: string[][] = []
      index += 1

      while (index < lines.length) {
        const line = lines[index]
        if (line.startsWith('|')) {
          rows.push(parseRow(line))
        }
        index += 1
        if (
          line.startsWith('+') &&
          !lines[index]?.startsWith('|') &&
          !lines[index]?.startsWith('+')
        ) {
          break
        }
      }

      if (rows.length > 0) {
        blocks.push({
          headers: rows[0],
          kind: 'table',
          rows: rows.slice(1),
        })
      }
      continue
    }

    blocks.push({
      kind: 'text',
      value: lines[index],
    })
    index += 1
  }

  return blocks
}
