import { describe, expect, it } from 'vitest'

import {
  filterResourceSearchEntries,
  getResourceSearchSpec,
  normalizeGlobPattern,
  retrievalItemsToEntries,
} from './find-search'
import type { FindResultItem } from '#/lib/retrieval'
import type { VikingFsEntry } from '../-types/viking-fm'

function entry(uri: string, isDir = false): VikingFsEntry {
  return {
    uri,
    name: uri.replace(/\/$/, '').split('/').pop() || uri,
    isDir,
    size: '',
    sizeBytes: null,
    modTime: '',
    modTimestamp: null,
    abstract: '',
  }
}

describe('resource path search', () => {
  it('roots exact file path searches at the containing directory', () => {
    expect(
      getResourceSearchSpec(
        'viking://resources/project/deep/file.md',
        'viking://',
      ),
    ).toEqual({
      mode: 'path',
      query: 'viking://resources/project/deep/file.md',
      rootUri: 'viking://resources/project/deep/',
    })
  })

  it('keeps directory path searches scoped to that subtree', () => {
    expect(
      getResourceSearchSpec('viking://resources/project/deep/', 'viking://'),
    ).toEqual({
      mode: 'path',
      query: 'viking://resources/project/deep/',
      rootUri: 'viking://resources/project/deep/',
    })
  })

  it('matches exact files and descendant directory entries', () => {
    const spec = getResourceSearchSpec(
      'viking://resources/project/deep',
      'viking://',
    )

    expect(
      filterResourceSearchEntries(
        [
          entry('viking://resources/project/deep.md'),
          entry('viking://resources/project/deep/', true),
          entry('viking://resources/project/deep/child.md'),
          entry('viking://resources/project/other.md'),
        ],
        spec,
      ).map((item) => item.uri),
    ).toEqual([
      'viking://resources/project/deep/',
      'viking://resources/project/deep/child.md',
    ])
  })
})

describe('normalizeGlobPattern', () => {
  it('prefixes bare patterns so they match at any depth', () => {
    expect(normalizeGlobPattern('*.md')).toBe('**/*.md')
    expect(normalizeGlobPattern('  *.md  ')).toBe('**/*.md')
  })

  it('leaves anchored patterns untouched', () => {
    expect(normalizeGlobPattern('docs/*.md')).toBe('docs/*.md')
    expect(normalizeGlobPattern('**/*.md')).toBe('**/*.md')
  })
})

describe('retrievalItemsToEntries', () => {
  function item(overrides: Partial<FindResultItem>): FindResultItem {
    return {
      uri: 'viking://resources/a.md',
      context_type: 'resources',
      level: 0,
      score: 0,
      abstract: '',
      category: '',
      match_reason: '',
      ...overrides,
    }
  }

  it('appends the line number and keeps the snippet for grep hits', () => {
    const [hit] = retrievalItemsToEntries({
      memories: [],
      resources: [
        item({ abstract: 'hit line', line: 42, result_kind: 'grep' }),
      ],
      skills: [],
      total: 1,
    })

    expect(hit.name).toBe('a.md:42')
    expect(hit.size).toBe('')
    expect(hit.abstract).toBe('hit line')
  })

  it('shows the score for semantic hits and nothing for glob hits', () => {
    const entries = retrievalItemsToEntries({
      memories: [],
      resources: [
        item({ result_kind: 'semantic', score: 0.8421 }),
        item({ uri: 'viking://resources/b.md', result_kind: 'glob' }),
      ],
      skills: [],
      total: 2,
    })

    expect(entries[0].size).toBe('0.84')
    expect(entries[0].name).toBe('a.md')
    expect(entries[1].size).toBe('')
  })

  it('returns nothing without a result', () => {
    expect(retrievalItemsToEntries(undefined)).toEqual([])
  })
})
