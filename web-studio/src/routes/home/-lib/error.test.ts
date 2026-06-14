import { describe, expect, it } from 'vitest'

import { OvClientError } from '#/lib/ov-client/errors'

import { resolveQueryErrorMessage } from './error'

describe('resolveQueryErrorMessage', () => {
  it('surfaces the OvClientError message', () => {
    const error = new OvClientError({
      code: 'UNAUTHENTICATED',
      message: 'Missing API key.',
      statusCode: 401,
    })
    expect(resolveQueryErrorMessage(error)).toBe(error.message)
  })

  it('falls back to a generic Error message', () => {
    expect(resolveQueryErrorMessage(new Error('Network Error'))).toBe(
      'Network Error',
    )
  })

  it('returns undefined for a blank OvClientError message', () => {
    const error = new OvClientError({ code: 'ERROR', message: '   ' })
    expect(resolveQueryErrorMessage(error)).toBeUndefined()
  })

  it('returns undefined for a blank generic Error message', () => {
    expect(resolveQueryErrorMessage(new Error('   '))).toBeUndefined()
  })

  it('returns undefined for non-error values', () => {
    expect(resolveQueryErrorMessage('nope')).toBeUndefined()
    expect(resolveQueryErrorMessage(undefined)).toBeUndefined()
    expect(resolveQueryErrorMessage(null)).toBeUndefined()
  })
})
