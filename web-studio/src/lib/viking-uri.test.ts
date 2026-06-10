import { describe, expect, it } from 'vitest'

import { cleanVikingUri } from './viking-uri'

describe('cleanVikingUri', () => {
  it('keeps spaces in direct viking uri values', () => {
    expect(
      cleanVikingUri(
        'viking://user/default/memories/events/2026/06/01/OpenViking Agent文档国际化调整.md',
      ),
    ).toBe(
      'viking://user/default/memories/events/2026/06/01/OpenViking Agent文档国际化调整.md',
    )
  })

  it('still extracts a uri from prose', () => {
    expect(cleanVikingUri('open viking://user/default/memory.md now')).toBe(
      'viking://user/default/memory.md',
    )
  })
})
