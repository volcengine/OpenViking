// @vitest-environment jsdom

import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router'
import { cleanup, render, screen } from '@testing-library/react'
import type { TFunction } from 'i18next'
import { createInstance } from 'i18next'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { RetrievalDetailSheet } from '#/features/workbench/retrieval/components/retrieval-detail-sheet'
import { resources } from '#/i18n/resources'

const serviceState = vi.hoisted(() => ({ provider: 'volcengine' }))

vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => serviceState,
}))

const t = ((key: string) => key) as TFunction<'retrieval'>

afterEach(() => {
  cleanup()
  serviceState.provider = 'volcengine'
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
    ['en', 'Open in Filesystem', 'viking://resources/guide.md', 2],
    ['zh-CN', '在文件系统中打开', 'viking://resources/guide.md', 2],
    ['zh-CN', '在文件系统中打开', 'viking://resources/guide/.abstract.md', 0],
    ['en', 'Open in Filesystem', 'viking://resources/guide/.overview.md', 1],
  ])(
    'links to the selected file with its translated label in %s for %s: %s (L%s)',
    async (lng, label, uri, level) => {
      serviceState.provider = 'opensource'
      const i18n = createInstance()
      await i18n.use(initReactI18next).init({ lng, resources })
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
                  level,
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
      expect(url.searchParams.get('uri')).toBe(
        uri.slice(0, uri.lastIndexOf('/') + 1),
      )
    },
  )
})
