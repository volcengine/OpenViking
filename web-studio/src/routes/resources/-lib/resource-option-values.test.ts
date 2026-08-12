import { describe, expect, it } from 'vitest'

import {
  parseDelimitedValues,
  parseOptionalNumber,
  parseResourceTags,
} from './resource-option-values'

describe('resource option values', () => {
  it('parses comma and newline separated values', () => {
    expect(parseDelimitedValues('/docs, /zh/\n/changelog')).toEqual([
      '/docs',
      '/zh/',
      '/changelog',
    ])
  })

  it('converts optional numeric values without applying server rules', () => {
    expect(parseOptionalNumber('2')).toBe(2)
    expect(parseOptionalNumber('-1')).toBe(-1)
    expect(parseOptionalNumber('1.5')).toBe(1.5)
    expect(parseOptionalNumber('')).toBeUndefined()
  })

  it('normalizes resource tags without duplicating server validation', () => {
    expect(parseResourceTags('team=docs, env=test')).toEqual([
      'team=docs',
      'env=test',
    ])
    expect(parseResourceTags('team=docs, invalid')).toEqual([
      'team=docs',
      'invalid',
    ])
  })
})
