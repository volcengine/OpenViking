import { describe, expect, it } from 'vitest'

import type { VikingFsEntry } from '../-types/viking-fm'
import {
  filterResourceSearchEntries,
  getResourceSearchSpec,
  normalizeVikingPathSearchQuery,
} from './find-search'

function entry(uri: string, isDir = false): VikingFsEntry {
  const normalizedUri = isDir && !uri.endsWith('/') ? `${uri}/` : uri
  const name = normalizedUri.replace(/\/$/, '').split('/').pop() || normalizedUri

  return {
    uri: normalizedUri,
    name,
    isDir,
    size: '',
    sizeBytes: null,
    modTime: '',
    modTimestamp: null,
    abstract: '',
  }
}

const entries = [
  entry('viking://resources/OpenViking/README.md'),
  entry('viking://resources/OpenViking/docs/', true),
  entry('viking://resources/OpenViking/docs/search-guide.md'),
  entry('viking://resources/playground/search-notes.txt'),
]

describe('resource find search', () => {
  it('matches directory and file names by keyword fuzzily', () => {
    const spec = getResourceSearchSpec(' search ', 'viking://resources/OpenViking/')

    expect(filterResourceSearchEntries(entries, spec).map((item) => item.uri)).toEqual([
      'viking://resources/OpenViking/docs/search-guide.md',
    ])
  })

  it('normalizes viking path queries while preserving a trailing slash', () => {
    expect(normalizeVikingPathSearchQuery(' viking://resources//OpenViking/docs/ ')).toBe(
      'viking://resources/OpenViking/docs/',
    )
  })

  it('matches files and directories by viking uri prefix', () => {
    const spec = getResourceSearchSpec('viking://resources/OpenViking/docs', 'viking://resources/')

    expect(filterResourceSearchEntries(entries, spec).map((item) => item.uri)).toEqual([
      'viking://resources/OpenViking/docs/',
      'viking://resources/OpenViking/docs/search-guide.md',
    ])
  })
})
