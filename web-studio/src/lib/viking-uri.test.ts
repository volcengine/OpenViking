import { describe, expect, it } from 'vitest'

import {
  cleanVikingUri,
  isDirectorySemanticSidecarUri,
  joinUri,
  retrievalResultNameFromUri,
} from './viking-uri'

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

describe('retrievalResultNameFromUri', () => {
  it.each(['.abstract.md', '.overview.md'])(
    'adds the summarized directory to %s results',
    (sidecar) => {
      const uri = `viking://resources/openviking-release/${sidecar}`

      expect(isDirectorySemanticSidecarUri(uri)).toBe(true)
      expect(retrievalResultNameFromUri(uri)).toBe(
        `openviking-release/${sidecar}`,
      )
    },
  )

  it('keeps ordinary result names unchanged', () => {
    expect(
      retrievalResultNameFromUri(
        'viking://resources/openviking-release/README.md',
      ),
    ).toBe('README.md')
  })
})

describe('joinUri', () => {
  it.each(['.abstract.md', '.overview.md'])(
    'does not build a scope-less uri when joining %s onto the bare root',
    (sidecar) => {
      expect(joinUri('viking://', sidecar)).toBe('viking://')
    },
  )

  it('still joins ordinary sidecars under a real scope', () => {
    expect(joinUri('viking://resources/foo/', '.abstract.md')).toBe(
      'viking://resources/foo/.abstract.md',
    )
  })

  it('still joins ordinary file names under the bare root', () => {
    expect(joinUri('viking://', 'resources/')).toBe('viking://resources/')
  })
})
