// @vitest-environment jsdom
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import type * as SessionApi from './api'
import { afterEach, expect, it, vi } from 'vitest'
import { useSessionMessages } from './use-sessions'

const state = vi.hoisted(() => ({
  connection: { baseUrl: 'http://oss.test', serviceSelection: 'opensource' },
  fetchMessages: vi.fn(),
}))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ connection: state.connection }),
}))
vi.mock('./api', async (original) => ({
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
