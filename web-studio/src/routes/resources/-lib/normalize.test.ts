import { describe, expect, it } from 'vitest'

import { beautifyJson, shouldAutoRead } from './normalize'

describe('beautifyJson', () => {
  it('formats valid JSON and leaves invalid JSON untouched', () => {
    expect(beautifyJson('{"beam":{"length":4200}}')).toBe(
      '{\n  "beam": {\n    "length": 4200\n  }\n}',
    )
    expect(beautifyJson('{broken')).toBe('{broken')
  })
})

describe('shouldAutoRead', () => {
  const unknownSizeJson = {
    isDir: false,
    uri: 'viking://resources/drawing.json',
    sizeBytes: null,
  }

  it('only blocks unknown sizes when the caller requires metadata', () => {
    expect(shouldAutoRead(unknownSizeJson).shouldRead).toBe(true)
    expect(shouldAutoRead(unknownSizeJson, 2 * 1024 * 1024, true)).toEqual({
      shouldRead: false,
      reason: 'too-large',
    })
  })
})
