// @vitest-environment jsdom
import { createServer } from 'node:http'
import { once } from 'node:events'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'

import { AppConnectionProvider, useAppConnection } from './use-app-connection'

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useRouterState: () => '/home',
}))

afterEach(cleanup)

function Identity() {
  const { connectionRole, isConnectionRoleLoading } = useAppConnection()
  return (
    <output data-testid="identity">
      {isConnectionRoleLoading ? 'loading' : connectionRole}
    </output>
  )
}

it('resolves a keyless trusted user on cold startup through the health transport', async () => {
  const observed: Array<[string | undefined, string | undefined]> = []
  const server = createServer((req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*')
    res.setHeader(
      'Access-Control-Allow-Headers',
      'X-OpenViking-Account, X-OpenViking-User, X-API-Key',
    )
    if (req.method === 'OPTIONS') {
      res.end()
      return
    }
    const account = req.headers['x-openviking-account'] as string | undefined
    const user = req.headers['x-openviking-user'] as string | undefined
    observed.push([account, user])
    res.setHeader('Content-Type', 'application/json')
    res.end(
      JSON.stringify({
        status: 'ok',
        auth_mode: 'trusted',
        root_api_key_required: false,
        ...(account && user
          ? { account_id: account, user_id: user, role: 'user' }
          : {}),
      }),
    )
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address() as { port: number }
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  localStorage.setItem(
    'ov_console_connection',
    JSON.stringify({
      baseUrl: `http://127.0.0.1:${address.port}`,
      accountId: 'account-a',
      userId: 'alice',
      apiKey: '',
      adminApiKey: '',
    }),
  )
  try {
    render(
      <QueryClientProvider client={queryClient}>
        <AppConnectionProvider>
          <Identity />
        </AppConnectionProvider>
      </QueryClientProvider>,
    )
    await waitFor(() =>
      expect(screen.getByTestId('identity').textContent).toBe('user'),
    )
    expect(observed).toEqual([['account-a', 'alice']])
  } finally {
    cleanup()
    queryClient.clear()
    localStorage.removeItem('ov_console_connection')
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})
