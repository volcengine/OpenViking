import { describe, expect, it } from 'vitest'

import en from '#/i18n/locales/en'
import zhCN from '#/i18n/locales/zh-CN'

function flatten(value: unknown, prefix = ''): Record<string, string> {
  if (typeof value === 'string') return { [prefix]: value }
  return Object.entries(value as Record<string, unknown>).reduce(
    (entries, [key, child]) => ({
      ...entries,
      ...flatten(child, prefix ? `${prefix}.${key}` : key),
    }),
    {},
  )
}

function placeholders(text: string): string[] {
  return [...text.matchAll(/\{\{(\w+)\}\}/g)].map((match) => match[1]).sort()
}

describe('contextGateway translations', () => {
  const english = flatten(en.contextGateway)
  const chinese = flatten(zhCN.contextGateway)

  it('have the same keys in English and Chinese', () => {
    expect(Object.keys(chinese).sort()).toEqual(Object.keys(english).sort())
  })

  it('use the same interpolation values and no empty strings', () => {
    for (const [key, text] of Object.entries(english)) {
      expect(text, key).not.toBe('')
      expect(chinese[key], key).not.toBe('')
      expect(placeholders(chinese[key] ?? ''), key).toEqual(placeholders(text))
    }
  })

  it('label the sidebar entry in both languages', () => {
    expect(en.appShell.footer.contextGateway).toBe('Context Gateway')
    expect(zhCN.appShell.footer.contextGateway).toBe('上下文网关')
  })

  it('summarize compaction and tool counts with their values', () => {
    for (const locale of [en, zhCN]) {
      const { summary } = locale.contextGateway.profiles
      expect(placeholders(summary.compactionOn)).toEqual(['percent'])
      for (const key of ['toolsEnabled_one', 'toolsEnabled_other'] as const) {
        expect(placeholders(summary[key])).toEqual(['count'])
      }
    }
  })
})
