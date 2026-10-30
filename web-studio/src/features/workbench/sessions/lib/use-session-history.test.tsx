// @vitest-environment jsdom
import type * as SessionApi from '#/features/workbench/sessions/lib/api'
import { useSessionMessages } from '#/features/workbench/sessions/lib/use-sessions'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  connection: { baseUrl: 'http://oss.test', serviceSelection: 'opensource' },
  fetchMessages: vi.fn(),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ connection: state.connection }),
}))
vi.mock('#/features/workbench/sessions/lib/api', async (original) => ({
  ...(await original<typeof SessionApi>()),
  fetchSession: vi.fn().mockResolvedValue({ commit_count: 1 }),
  fetchSessionMessages: state.fetchMessages,
}))
afterEach(cleanup)

it('selects the matching history adapter without requiring the view to know transport', async () => {
  state.connection = {
    baseUrl: 'http://oss.test',
    serviceSelection: 'opensource',
  }
  state.fetchMessages.mockImplementation(async (_id, _meta, source) => [
    { id: source, role: 'user', parts: [{ type: 'text', text: source }] },
  ])
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  try {
    const view = renderHook(() => useSessionMessages('s'), { wrapper })
    await waitFor(() => expect(view.result.current.data?.[0].id).toBe('api'))
    state.connection = {
      baseUrl: 'https://api.vikingdb.cn-beijing.volces.com/openviking',
      serviceSelection: 'opensource',
    }
    view.rerender()
    await waitFor(() =>
      expect(view.result.current.data?.[0].id).toBe('content'),
    )
    state.connection = {
      baseUrl: 'http://oss.test',
      serviceSelection: 'opensource',
    }
    view.rerender()
    await waitFor(() => expect(view.result.current.data?.[0].id).toBe('api'))
  } finally {
    client.clear()
  }
})
