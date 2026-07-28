import { describe, expect, it } from 'vitest'

import { isDuplicateDisplayContent } from './results'

describe('retrieval result display', () => {
  it('treats summary and content with whitespace-only differences as duplicates', () => {
    expect(
      isDuplicateDisplayContent(
        '# Title\n\nThe same content.',
        '  # Title\r\nThe same   content.  ',
      ),
    ).toBe(true)

    expect(
      isDuplicateDisplayContent(
        '# Title\n\nA short summary.',
        '# Title\n\nThe full content.',
      ),
    ).toBe(false)
  })
})
