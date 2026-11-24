import { describe, expect, it } from 'vitest'
import { resources } from './resources'

function flatten(value: unknown, prefix = ''): Record<string, string> {
  if (typeof value === 'string') return { [prefix]: value }
  return Object.assign(
    {},
    ...Object.entries(value as Record<string, unknown>).map(([key, child]) =>
      flatten(child, prefix ? `${prefix}.${key}` : key),
    ),
  )
}

function placeholders(text: string): string[] {
  return [...text.matchAll(/\{\{-?\s*([^},\s]+)[^}]*\}\}/g)]
    .map((match) => match[1])
    .sort()
}

describe('all Studio translation resources', () => {
  const english = flatten(resources.en)
  const chinese = flatten(resources['zh-CN'])

  it('registers matching keys in both languages', () => {
    expect(Object.keys(chinese).sort()).toEqual(Object.keys(english).sort())
  })

  it('has no empty translations or mismatched interpolation variables', () => {
    for (const [key, text] of Object.entries(english)) {
      expect(text.trim(), key).not.toBe('')
      expect(chinese[key].trim(), key).not.toBe('')
      expect(placeholders(chinese[key] ?? ''), key).toEqual(placeholders(text))
    }
  })
})
