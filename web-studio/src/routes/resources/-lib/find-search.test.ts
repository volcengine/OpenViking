import { describe, expect, it } from 'vitest'

import {
  filterResourceSearchEntries,
  getResourceSearchSpec,
} from './find-search'
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
