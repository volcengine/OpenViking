import type { TFunction } from 'i18next'
import { describe, expect, it } from 'vitest'

import { localizeObserverStatusBlocks } from './localize-observer-status'

const translations: Record<string, string> = {
  'detail.columns.contextType': '上下文类型',
  'detail.columns.metric': '指标',
  'detail.columns.queries': '查询次数',
  'detail.columns.value': '数值',
  'detail.metrics.totalOperations': '操作总数',
  'detail.statusText.mount': '挂载点：{{path}}（插件：{{plugin}}）',
  'detail.values.total': '合计',
  'detail.values.unknown': '未知',
  'queue.totalRow': '合计',
}

const t = ((key: string, options?: Record<string, unknown>) => {
  let value = translations[key] ?? key
  for (const [name, replacement] of Object.entries(options ?? {})) {
    value = value.replace(`{{${name}}}`, String(replacement))
  }
  return value
}) as TFunction<'monitoringPage'>

describe('localizeObserverStatusBlocks', () => {
  it('localizes registered observer labels and preserves technical values', () => {
    expect(
      localizeObserverStatusBlocks(
        [
          {
            headers: ['Metric', 'Value'],
            kind: 'table',
            rows: [['Total Operations', '12']],
          },
          {
            headers: ['Context Type', 'Queries'],
            kind: 'table',
            rows: [
              ['unknown', '8'],
              ['resource', '4'],
            ],
          },
          {
            kind: 'text',
            value: 'Mount: /local (plugin: localfs)',
          },
        ],
        t,
      ),
    ).toEqual([
      {
        headers: ['指标', '数值'],
        kind: 'table',
        rows: [['操作总数', '12']],
      },
      {
        headers: ['上下文类型', '查询次数'],
        kind: 'table',
        rows: [
          ['未知', '8'],
          ['resource', '4'],
        ],
      },
      {
        kind: 'text',
        value: '挂载点：/local（插件：localfs）',
      },
    ])
  })

  it('localizes total rows only in summary columns', () => {
    const [block] = localizeObserverStatusBlocks(
      [
        {
          headers: ['Queue', 'Collection', 'Provider', 'Model', 'Operation'],
          kind: 'table',
          rows: [['TOTAL', 'TOTAL', 'TOTAL', 'TOTAL', 'TOTAL']],
        },
      ],
      t,
    )

    expect(block).toMatchObject({
      rows: [['合计', '合计', 'TOTAL', 'TOTAL', 'TOTAL']],
    })
  })
})
