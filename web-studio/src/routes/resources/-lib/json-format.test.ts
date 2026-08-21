import { describe, expect, it } from 'vitest'

import { formatJsonContent } from './json-format'

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

  it('throws for invalid JSON', () => {
    expect(() => formatJsonContent('{"invalid"}')).toThrow()
  })
})
