// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ObserverStatusContent } from './observer-status-content'

const translations: Record<string, string> = {
  'detail.columns.contextType': '上下文类型',
  'detail.columns.count': '次数',
  'detail.columns.metric': '指标',
  'detail.columns.operation': '操作',
  'detail.columns.queries': '查询次数',
  'detail.columns.value': '数值',
  'detail.metrics.totalOperations': '操作总数',
  'detail.values.unknown': '未知',
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => translations[key] ?? key,
  }),
}))

afterEach(cleanup)

describe('ObserverStatusContent', () => {
  it('renders localized labels from adjacent observer tables', () => {
    render(
      <ObserverStatusContent
        status={`
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
+--------------+---------+
| Context Type | Queries |
+--------------+---------+
| unknown      | 8       |
+--------------+---------+
`}
      />,
    )

    expect(screen.getByRole('columnheader', { name: '指标' })).toBeTruthy()
    expect(screen.getByText('操作总数')).toBeTruthy()
    expect(
      screen.getByRole('columnheader', { name: '上下文类型' }),
    ).toBeTruthy()
    expect(screen.getByText('未知')).toBeTruthy()
    expect(screen.queryByText('Metric')).toBeNull()
    expect(screen.queryByText('unknown')).toBeNull()
  })
})
