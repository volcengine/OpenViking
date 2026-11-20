// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

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

it('shows and filters memory changes across Bot session resets', async () => {
  getSessionBySessionId.mockImplementation(({ path }) =>
    response({
      commit_count: 1,
      uri: `viking://user/default/sessions/${path.session_id}`,
    }),
  )
  getContentRead.mockImplementation(({ query }) => {
    const kind = query.uri.includes('/bot-original/')
      ? 'profile'
      : 'preferences'
    return response({
      operations: {
        adds: [{ memory_type: kind, uri: `viking://user/${kind}` }],
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
  fireEvent.click(screen.getByRole('button', { name: 'impact.open' }))
  expect(await screen.findAllByText('archive_001')).toHaveLength(2)

  fireEvent.click(screen.getByRole('tab', { name: 'profile' }))
  expect(screen.getByText('viking://user/profile')).toBeTruthy()
  expect(screen.queryByText('viking://user/preferences')).toBeNull()
})
