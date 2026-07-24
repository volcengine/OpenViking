import { describe, expect, it } from 'vitest'

import { parseObserverStatus } from './parse-status'

describe('parseObserverStatus', () => {
  it('converts an ASCII table into structured rows', () => {
    expect(
      parseObserverStatus(`
+-------+---------+
| Queue | Pending |
+-------+---------+
| Embed | 2       |
+-------+---------+
`),
    ).toEqual([
      {
        headers: ['Queue', 'Pending'],
        kind: 'table',
        rows: [['Embed', '2']],
      },
    ])
  })

  it('keeps plain status messages as text', () => {
    expect(parseObserverStatus('No active locks.')).toEqual([
      {
        kind: 'text',
        value: 'No active locks.',
      },
    ])
  })
})
