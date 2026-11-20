// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { MemoryImpact } from '#/features/workbench/sessions/components/memory-impact'
import type { SessionMeta } from '@ov-server/api/v1/sessions'

const { getSessionBySessionId, getContentRead } = vi.hoisted(() => ({
  getSessionBySessionId: vi.fn(),
  getContentRead: vi.fn(),
}))

vi.mock('#/gen/ov-client/sdk.gen', () => ({
  getSessionBySessionId,
  getContentRead,
}))

vi.mock('react-i18next', () => ({
  initReactI18next: { init: vi.fn() },
  useTranslation: () => ({
    i18n: { resolvedLanguage: 'en' },
    t: (key: string) => key,
  }),
}))

afterEach(cleanup)

function response(result: unknown) {
  return Promise.resolve({
    data: { status: 'ok', result },
    headers: {},
    status: 200,
  })
}

describe('MemoryImpact', () => {
  it('shows memory changes across Bot session resets and filters by memory type', async () => {
    const baseUri = 'viking://user/default/sessions'
    getSessionBySessionId.mockImplementation(({ path }) =>
      response({
        session_id: path.session_id,
        commit_count: 1,
        uri: `${baseUri}/${path.session_id}`,
      }),
    )
    getContentRead.mockImplementation(({ query }) => {
      const original = query.uri.includes('/bot-original/')
      return response({
        extracted_at: original
          ? '2026-07-26T00:00:00Z'
          : '2026-07-26T01:00:00Z',
        operations: original
          ? {
              adds: [{ memory_type: 'profile', uri: 'viking://user/profile' }],
              deletes: [
                { memory_type: 'preferences', uri: 'viking://user/preference' },
              ],
            }
          : {
              updates: [
                {
                  memory_type: 'preferences',
                  uri: 'viking://user/preference-2',
                },
              ],
            },
      })
    })

    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryImpact
          session={{ session_id: 'web', commit_count: 0 } as SessionMeta}
          linkedSessionIds={['bot-original', 'bot-after-new']}
        />
      </QueryClientProvider>,
    )

    expect(getSessionBySessionId).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'impact.open' }))
    expect(await screen.findAllByText('archive_001')).toHaveLength(2)
    fireEvent.click(screen.getByRole('tab', { name: 'profile' }))

    const visibleArchive = screen.getByText('archive_001').closest('section')
    expect(visibleArchive?.textContent).toContain('+1')
    expect(visibleArchive?.textContent).not.toContain('−1')
    expect(screen.queryByText('viking://user/preference-2')).toBeNull()
  })
})
