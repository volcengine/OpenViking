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

  it('keeps adjacent ASCII tables as separate blocks', () => {
    expect(
      parseObserverStatus(`
+-----------+-------+
| Operation | Count |
+-----------+-------+
| read      | 12    |
+-----------+-------+
+------------------+-------+
| Metric           | Value |
+------------------+-------+
| Total Operations | 12    |
+------------------+-------+
`),
    ).toEqual([
      {
        headers: ['Operation', 'Count'],
        kind: 'table',
        rows: [['read', '12']],
      },
      {
        headers: ['Metric', 'Value'],
        kind: 'table',
        rows: [['Total Operations', '12']],
      },
    ])
  })
})
