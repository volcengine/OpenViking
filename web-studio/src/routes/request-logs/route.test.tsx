// @vitest-environment jsdom
import type { ComponentType } from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'

import { Route } from './route'
import type * as AuditApi from './-lib/api'

const { fetchLogs } = vi.hoisted(() => ({ fetchLogs: vi.fn() }))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'http://localhost:1933',
      accountId: 'account-a',
      userId: 'alice',
      apiKey: '',
      adminApiKey: '',
    },
    connectionRole: 'user',
    isConnectionRoleLoading: false,
  }),
}))
vi.mock('./-lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof AuditApi>()),
  fetchAuditLogs: fetchLogs,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(cleanup)

it.each(['loading', 'disabled', 'error', 'zero'])(
  'renders audit metrics for %s',
  async (state) => {
    fetchLogs.mockReset()
    if (state === 'loading')
      fetchLogs.mockImplementation(() => new Promise(() => {}))
    if (state === 'disabled') fetchLogs.mockResolvedValue({ enabled: false })
    if (state === 'error')
      fetchLogs.mockRejectedValue(new Error('Request failed'))
    if (state === 'zero')
      fetchLogs.mockResolvedValue({ items: [], total: 0, success_rate: 0 })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const Logs = Route.options.component as ComponentType & {
      preload?: () => Promise<unknown>
    }
    await Logs.preload?.()
    const view = render(
      <QueryClientProvider client={client}>
        <Logs />
      </QueryClientProvider>,
    )
    try {
      if (state === 'disabled') await screen.findByText('disabled.title')
      if (state === 'error') await screen.findByText('error.title')
      if (state === 'zero') await screen.findByText('empty.title')
      if (state === 'zero') {
        await waitFor(() => expect(screen.getByText('0%')).toBeTruthy())
        expect(screen.queryByText('—')).toBeNull()
      } else {
        await waitFor(() => expect(screen.getAllByText('—')).toHaveLength(2))
      }
    } finally {
      view.unmount()
      client.clear()
    }
  },
)
