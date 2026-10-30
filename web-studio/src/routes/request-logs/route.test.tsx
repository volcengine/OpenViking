// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import type { ComponentType } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import type * as AuditApi from '#/features/workbench/request-logs/lib/api'
import { Route } from '#/routes/request-logs/route'

const { fetchLogs } = vi.hoisted(() => ({ fetchLogs: vi.fn() }))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
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
vi.mock(
  '#/features/workbench/request-logs/lib/api',
  async (importOriginal) => ({
    ...(await importOriginal<typeof AuditApi>()),
    fetchAuditLogs: fetchLogs,
  }),
)
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
