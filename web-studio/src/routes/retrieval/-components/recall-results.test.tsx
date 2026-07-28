// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { TFunction } from 'i18next'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { RecallResults } from './recall-results'
import type { RecallResult } from '#/lib/retrieval'

const t = ((key: string) => key) as TFunction<'retrieval'>

afterEach(cleanup)

function recallResult(rendered: string): RecallResult {
  return {
    entries: [
      {
        content: 'Preference body',
        mode: 'full',
        rank: 1,
        score: 0.82,
        type: 'preferences',
        uri: 'viking://user/default/memories/preferences/api.md',
      },
    ],
    rendered,
    stats: {
      dropped: 0,
      max_chars: 6500,
      min_score: 0.1,
      origins: { self: 1 },
      peer_scope: 'all',
      quotas: { preferences: 3 },
      returned: 1,
      searched: { preferences: 1 },
    },
  }
}

describe('RecallResults', () => {
  it('returns to entries when a new result has no rendered text', () => {
    const { rerender } = render(
      <RecallResults
        onSelect={vi.fn()}
        result={recallResult('Rendered memory')}
        t={t}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'recall.rendered' }))
    expect(screen.getByText('Rendered memory')).toBeDefined()

    rerender(
      <RecallResults onSelect={vi.fn()} result={recallResult('')} t={t} />,
    )

    expect(screen.queryByText('Rendered memory')).toBeNull()
    expect(screen.getByText('api.md')).toBeDefined()
  })
})
