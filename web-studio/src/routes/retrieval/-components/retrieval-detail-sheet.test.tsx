// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router'
import { createInstance } from 'i18next'
import type { TFunction } from 'i18next'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { resources } from '#/i18n/resources'
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

  it.each([
    ['en', 'Open in Filesystem'],
    ['zh-CN', '在文件系统中打开'],
  ])(
    'links to the selected file with its translated label in %s',
    async (lng, label) => {
      const i18n = createInstance()
      await i18n.use(initReactI18next).init({ lng, resources })
      const uri = 'viking://resources/guide.md'
      const router = createRouter({
        basepath: '/studio',
        history: createMemoryHistory({ initialEntries: ['/studio/'] }),
        routeTree: createRootRoute({
          component: () => (
            <RetrievalDetailSheet
              detail={{
                contextType: 'resource',
                score: 0.8,
                uri,
                item: {
                  abstract: 'Guide summary',
                  category: '',
                  context_type: 'resource',
                  level: 2,
                  match_reason: '',
                  score: 0.8,
                  uri,
                },
              }}
              onClose={vi.fn()}
              t={i18n.getFixedT(lng, 'retrieval')}
            />
          ),
        }),
      })
      render(
        <I18nextProvider i18n={i18n}>
          <RouterProvider router={router} />
        </I18nextProvider>,
      )
      const link = (await screen.findByText(label)).closest('a')
      const url = new URL(link!.getAttribute('href')!, 'http://localhost')
      expect(url.pathname).toBe('/studio/filesystem')
      expect(url.searchParams.get('file')).toBe(uri)
      expect(url.searchParams.get('uri')).toBe('viking://resources/')
    },
  )
})
