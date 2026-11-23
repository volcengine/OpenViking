import { expect, it } from 'vitest'

import { resourceSearchForResult } from '#/features/workbench/retrieval/lib/results'
import type { FindResultItem } from '#/shared/api/retrieval'

it.each([0, 1])(
  'preserves directory results at L%s, including dotted directory names',
  (level) => {
    const item: FindResultItem = {
      uri: 'viking://resources/release.v1',
      context_type: 'resource',
      level,
      score: 0.8,
      abstract: '',
      category: '',
      match_reason: '',
    }
    expect(resourceSearchForResult(item)).toEqual({
      uri: 'viking://resources/release.v1/',
    })
  },
)
