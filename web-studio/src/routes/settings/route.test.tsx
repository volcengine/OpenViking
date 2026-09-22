// @vitest-environment jsdom
import type { ComponentType } from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'

import { Route } from './route'

const { probe } = vi.hoisted(() => ({ probe: vi.fn() }))
vi.mock('#/lib/admin', () => ({ probeStudioConnection: probe }))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'http://localhost:1933',
      accountId: 'account-a',
      userId: 'alice',
      adminApiKey: '',
      apiKey: '',
    },
    serverMode: 'trusted',
    saveConnection: vi.fn(),
  }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: 'en' },
  }),
}))
afterEach(cleanup)

it.each([false, undefined, true])(
  'bases the Root key guide on server metadata: %s',
  async (required) => {
    probe.mockResolvedValue({
      admin: {
        state: 'error',
        statusCode: 403,
        detail: 'Management unavailable',
      },
      data: { state: 'ok', detailCode: 'tenantDataAvailable' },
      rootApiKeyRequired: required,
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const Settings = Route.options.component as ComponentType & {
      preload?: () => Promise<unknown>
    }
    await Settings.preload?.()
    render(
      <QueryClientProvider client={client}>
        <Settings />
      </QueryClientProvider>,
    )
    await waitFor(
      () => expect(screen.getByText('Management unavailable')).toBeTruthy(),
      { timeout: 5000 },
    )
    expect(
      Boolean(screen.queryByText('connection.keyGuide.trusted.title')),
    ).toBe(required === true)
    client.clear()
  },
)
