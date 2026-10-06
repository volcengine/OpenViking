import { describe, expect, it } from 'vitest'

import { checkNumber, checkRequired, collect, isValid } from './validation'
import type { ValidationErrors } from './validation'

describe('checkNumber', () => {
  it('treats empty values as required unless optional', () => {
    expect(checkNumber(Number.NaN, {})).toEqual({ key: 'validation.required' })
    expect(checkNumber(null, {})).toEqual({ key: 'validation.required' })
    expect(checkNumber(null, { optional: true })).toBeUndefined()
  })

  it('reports the bound that was crossed', () => {
    const range = { min: 64, max: 32000, integer: true }
    expect(checkNumber(64, range)).toBeUndefined()
    expect(checkNumber(63, range)).toEqual({
      key: 'validation.range',
      values: { min: 64, max: 32000 },
    })
    expect(checkNumber(1.5, range)).toEqual({ key: 'validation.integer' })
    expect(checkNumber(-1, { min: 0 })).toEqual({
      key: 'validation.min',
      values: { min: 0 },
    })
    expect(checkNumber(2000, { max: 1000 })).toEqual({
      key: 'validation.max',
      values: { max: 1000 },
    })
    expect(checkNumber(Number.POSITIVE_INFINITY, {})).toEqual({
      key: 'validation.number',
    })
  })

  it('excludes the minimum for exclusive bounds', () => {
    expect(checkNumber(0, { min: 0, max: 30, exclusiveMin: true })).toEqual({
      key: 'validation.rangeExclusive',
      values: { min: 0, max: 30 },
    })
    expect(checkNumber(0, { min: 0, exclusiveMin: true })).toEqual({
      key: 'validation.greaterThan',
      values: { min: 0 },
    })
    expect(checkNumber(0.1, { min: 0, exclusiveMin: true })).toBeUndefined()
  })
})

it('collects the first problem per field', () => {
  const errors: ValidationErrors = {}
  collect(errors, 'name', checkRequired('  '))
  collect(errors, 'name', { key: 'validation.number' })
  collect(errors, 'other', undefined)
  expect(errors).toEqual({ name: { key: 'validation.required' } })
  expect(isValid(errors)).toBe(false)
  expect(isValid({})).toBe(true)
})
