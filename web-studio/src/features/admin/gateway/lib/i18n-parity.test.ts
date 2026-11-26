import { describe, expect, it } from 'vitest'

import en from '#/i18n/locales/en'
import zhCN from '#/i18n/locales/zh-CN'

function placeholders(text: string): string[] {
  return [...text.matchAll(/\{\{(\w+)\}\}/g)].map((match) => match[1]).sort()
}

describe('gateway translations', () => {
  it('summarize compaction and tool counts with their values', () => {
    for (const locale of [en, zhCN]) {
      const { summary } = locale.gateway.profiles
      expect(placeholders(summary.compactionOn)).toEqual(['percent'])
      for (const key of ['toolsEnabled_one', 'toolsEnabled_other'] as const) {
        expect(placeholders(summary[key])).toEqual(['count'])
      }
    }
  })
})
