import { describe, expect, it } from 'vitest'

import {
  filterResourceSearchEntries,
  getResourceSearchSpec,
  normalizeGlobPattern,
  resourceEntryAbstractForDisplay,
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

  it('adds parent context to L0/L1 semantic sidecar names only', () => {
    const entries = retrievalItemsToEntries({
      memories: [],
      resources: [
        item({
          level: 0,
          uri: 'viking://resources/openviking-release/.abstract.md',
        }),
        item({
          level: 1,
          uri: 'viking://resources/openviking-release/.overview.md',
        }),
        item({
          level: 2,
          uri: 'viking://resources/openviking-release/README.md',
        }),
      ],
      skills: [],
      total: 3,
    })

    expect(entries.map((resultEntry) => resultEntry.name)).toEqual([
      'openviking-release/.abstract.md',
      'openviking-release/.overview.md',
      'README.md',
    ])
  })

  it('returns nothing without a result', () => {
    expect(retrievalItemsToEntries(undefined)).toEqual([])
  })
})

describe('resourceEntryAbstractForDisplay', () => {
  it('shows only the body for a directory with sidecar frontmatter', () => {
    expect(
      resourceEntryAbstractForDisplay({
        ...entry(
          'viking://resources/openviking-contribute/pr-review-axiom/',
          true,
        ),
        abstract: [
          '---',
          'directory: viking://resources/openviking-contribute/pr-review-axiom/',
          'generated_by:',
          '  component: SemanticProcessor',
          '  trigger: parent_refresh',
          '---',
          '',
          'PR review guidance',
        ].join('\n'),
      }),
    ).toBe('PR review guidance')
  })

  it('hides clipped directory frontmatter when the list payload has no body', () => {
    expect(
      resourceEntryAbstractForDisplay({
        ...entry(
          'viking://resources/openviking-contribute/pr-review-axiom/',
          true,
        ),
        abstract:
          '---\ndirectory: viking://resources/openviking-contribute/pr-review-axiom/\ngenerated_by: ...',
      }),
    ).toBe('')
  })

  it('keeps legacy directory summaries and file abstracts unchanged', () => {
    expect(
      resourceEntryAbstractForDisplay({
        ...entry('viking://resources/legacy/', true),
        abstract: 'Legacy summary',
      }),
    ).toBe('Legacy summary')
    expect(
      resourceEntryAbstractForDisplay({
        ...entry('viking://resources/user.md'),
        abstract: '---\ntitle: user content\n---',
      }),
    ).toBe('---\ntitle: user content\n---')
    expect(
      resourceEntryAbstractForDisplay({
        ...entry('viking://resources/legacy/', true),
        abstract: '---\n\nLegacy summary',
      }),
    ).toBe('---\n\nLegacy summary')
    expect(
      resourceEntryAbstractForDisplay({
        ...entry('viking://resources/manual/', true),
        abstract:
          '---\ndirectory: viking://resources/manual/\n---\nManual summary',
      }),
    ).toBe('---\ndirectory: viking://resources/manual/\n---\nManual summary')
  })
})
