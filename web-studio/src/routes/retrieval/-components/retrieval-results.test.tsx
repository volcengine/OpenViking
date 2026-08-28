// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { createInstance } from 'i18next'
import type { TFunction } from 'i18next'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { resources } from '#/i18n/resources'

import { RetrievalResults } from './retrieval-results'

const t = ((key: string) => key) as TFunction<'retrieval'>

afterEach(cleanup)

describe('RetrievalResults', () => {
  it.each(['en', 'zh-CN'] as const)(
    'sizes the metadata column to fit translated labels in %s',
    async (language) => {
      const i18n = createInstance()
      await i18n.init({ lng: language, resources })
      const translate = i18n.getFixedT(language, 'retrieval')
      const queryClient = new QueryClient()

      render(
        <QueryClientProvider client={queryClient}>
          <RetrievalResults
            data={{
              memories: [],
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
            t={translate}
          />
        </QueryClientProvider>,
      )

      const label = screen.getByText(translate('results.description'))
      expect(
        label
          .closest('dl')
          ?.classList.contains('grid-cols-[max-content_minmax(0,1fr)]'),
      ).toBe(true)
      expect(
        screen.getByText('Result summary').classList.contains('line-clamp-2'),
      ).toBe(true)
      expect(
        screen
          .getByText('viking://resources/result.md')
          .classList.contains('truncate'),
      ).toBe(true)
    },
  )

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
