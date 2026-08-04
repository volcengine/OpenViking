// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import type { TFunction } from 'i18next'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { RetrievalDetailSheet } from './retrieval-detail-sheet'

const t = ((key: string) => key) as TFunction<'retrieval'>

afterEach(() => {
  cleanup()
})

describe('RetrievalDetailSheet', () => {
  it('shows only the result summary without loading full content', () => {
    render(
      <RetrievalDetailSheet
        detail={{
          abstract: 'Result summary',
          contextType: 'memory',
          score: 0.8,
          uri: 'viking://user/default/memories/test.md',
        }}
        onClose={vi.fn()}
        t={t}
      />,
    )

    expect(screen.getByText('detail.summary')).toBeDefined()
    expect(screen.getByText('Result summary')).toBeDefined()
    expect(screen.queryByText('detail.content')).toBeNull()
  })
})
