import { describe, expect, it } from 'vitest'

import { formatJsonContent } from '#/features/workbench/resources/lib/json-format'

describe('formatJsonContent', () => {
  it('adds indentation and line breaks to compact JSON', () => {
    expect(formatJsonContent('{"items":[{"id":1},{"id":2}]}')).toBe(
      [
        '{',
        '  "items": [',
        '    {',
        '      "id": 1',
        '    },',
        '    {',
        '      "id": 2',
        '    }',
        '  ]',
        '}',
      ].join('\n'),
    )
  })

  it('preserves large numbers and exponent notation', () => {
    expect(formatJsonContent('{"id":9007199254740993,"scale":1e400,"zero":-0}')).toBe(
      '{\n  "id": 9007199254740993,\n  "scale": 1e400,\n  "zero": -0\n}',
    )
  })

  it('preserves escaped text and keeps empty containers compact', () => {
    const value = String.raw`"a \"quote\", [colon: \u0041]"`
    expect(formatJsonContent(`{"text":${value},"empty":[{},[]]}`)).toBe(
      ['{', `  "text": ${value},`, '  "empty": [', '    {},', '    []', '  ]', '}'].join('\n'),
    )
  })

  it('throws for invalid JSON', () => {
    expect(() => formatJsonContent('{"invalid"}')).toThrow()
  })
})
