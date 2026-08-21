// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import type { TFunction } from 'i18next'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { RetrievalResults } from './retrieval-results'

const t = ((key: string) => key) as TFunction<'retrieval'>

afterEach(cleanup)

describe('RetrievalResults', () => {
  it('exposes provenance returned by semantic retrieval', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    render(
      <QueryClientProvider client={queryClient}>
        <RetrievalResults
          data={{
            memories: [],
            provenance: [{ query: 'expanded query', stage: 'rerank' }],
            resources: [
              {
                abstract: 'Result summary',
                category: '',
                context_type: 'resource',
                level: 2,
                match_reason: '',
                score: 0.8,
                uri: 'viking://resources/result.md',
              },
            ],
            skills: [],
            total: 1,
          }}
          hasRetrievableContext
          hasSubmitted
          isCheckingContext={false}
          isError={false}
          isLoading={false}
          onUploadClick={vi.fn()}
          resultCount={10}
          t={t}
        />
      </QueryClientProvider>,
    )

    expect(screen.getByText('results.provenance')).toBeDefined()
    expect(screen.getByText(/expanded query/)).toBeDefined()
  })

  it('shows the API error message and diagnostic metadata', () => {
    render(
      <RetrievalResults
        error={{
          code: 'INVALID_ARGUMENT',
          message: 'The glob pattern is invalid.',
          requestId: 'req-123',
          statusCode: 400,
        }}
        hasRetrievableContext
        hasSubmitted
        isCheckingContext={false}
        isError
        isLoading={false}
        onUploadClick={vi.fn()}
        resultCount={10}
        t={t}
      />,
    )

    expect(screen.getByText('The glob pattern is invalid.')).toBeDefined()
    expect(screen.getByText(/INVALID_ARGUMENT/)).toBeDefined()
    expect(screen.getByText(/400/)).toBeDefined()
    expect(screen.getByText(/req-123/)).toBeDefined()
  })

  it('explains network failures instead of showing a generic error', () => {
    render(
      <RetrievalResults
        error={{ code: 'NETWORK_ERROR', message: 'Network Error' }}
        hasRetrievableContext
        hasSubmitted
        isCheckingContext={false}
        isError
        isLoading={false}
        onUploadClick={vi.fn()}
        resultCount={10}
        t={t}
      />,
    )

    expect(screen.getByText('error.network')).toBeDefined()
    expect(screen.queryByText('Network Error')).toBeNull()
  })
})
