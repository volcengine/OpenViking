import { describe, expect, it } from 'vitest'

import { shouldAutoRead } from './normalize'

const jsonEntry = {
  isDir: false,
  uri: 'viking://resources/example.json',
  sizeBytes: null,
}

describe('shouldAutoRead', () => {
  it('waits for JSON size metadata when required', () => {
    expect(shouldAutoRead(jsonEntry, 2 * 1024 * 1024, true)).toEqual({
      shouldRead: false,
      reason: 'unknown-size',
    })
  })

  it('automatically reads a known small JSON file', () => {
    expect(
      shouldAutoRead({ ...jsonEntry, sizeBytes: 1024 }, 2 * 1024 * 1024, true),
    ).toEqual({ shouldRead: true })
  })

  it('requires an explicit load for a known large JSON file', () => {
    expect(
      shouldAutoRead(
        { ...jsonEntry, sizeBytes: 3 * 1024 * 1024 },
        2 * 1024 * 1024,
        true,
      ),
    ).toEqual({ shouldRead: false, reason: 'too-large' })
  })
})
