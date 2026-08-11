import { describe, expect, it } from 'vitest'

import {
  isOptionalIntegerValid,
  parseDelimitedValues,
  parseOptionalInteger,
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

  it('accepts only optional bounded integers', () => {
    expect(parseOptionalInteger('2', 0)).toBe(2)
    expect(parseOptionalInteger('', 1)).toBeUndefined()
    expect(isOptionalIntegerValid('-1', 0)).toBe(false)
    expect(isOptionalIntegerValid('1.5', 0)).toBe(false)
  })

  it('validates resource tags as key=value pairs', () => {
    expect(parseResourceTags('team=docs, env=test')).toEqual({
      tags: ['team=docs', 'env=test'],
      valid: true,
    })
    expect(parseResourceTags('topic=machine learning')).toEqual({
      tags: ['topic=machine learning'],
      valid: true,
    })
    expect(parseResourceTags('team=docs, invalid')).toMatchObject({
      valid: false,
    })
    expect(parseResourceTags('team=docs=archive')).toMatchObject({
      valid: false,
    })
  })
})
