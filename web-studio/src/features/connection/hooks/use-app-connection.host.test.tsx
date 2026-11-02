// @vitest-environment jsdom
import {
  AppConnectionProvider,
  useAppConnection,
} from '#/features/connection/hooks/use-app-connection'
import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const { health, navigate } = vi.hoisted(() => ({
  health: vi.fn(),
  navigate: vi.fn(),
}))
vi.mock('#/features/connection/lib/health', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  fetchServerHealth: health,
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  useRouterState: () => '/',
}))
afterEach(() => {
  cleanup()
  delete window.__OV_STUDIO_CONFIG__
  localStorage.clear()
  health.mockReset()
  localStorage.removeItem('ov_console_connection')
  window.history.replaceState(null, '', '/')
})

it('does not show filesystem data cached for the previous user after re-entering from config', () => {
  const queryClient = new QueryClient()
  const key = ['viking-fs-ls', 'viking://', {}]
  queryClient.setQueryData(key, 'previous-user-files')
  localStorage.setItem(
    'ov_console_connection',
    JSON.stringify({
      baseUrl: 'https://current-ov.example',
      accountId: 'default',
      userId: 'new-user',
      apiKey: 'new-user-key',
      adminApiKey: '',
    }),
  )
  health.mockImplementation(() => new Promise(() => {}))
  function Filesystem() {
    const { data } = useQuery({
      queryKey: key,
      queryFn: () => new Promise<string>(() => {}),
      staleTime: 30_000,
    })
    return <output data-testid="files">{data ?? 'loading'}</output>
  }
  render(
    <QueryClientProvider client={queryClient}>
      <AppConnectionProvider>
        <Filesystem />
      </AppConnectionProvider>
    </QueryClientProvider>,
  )
  expect(screen.getByTestId('files').textContent).toBe('loading')
  queryClient.clear()
})

it('discovers the Studio gateway origin before using a stale saved Base URL or key', async () => {
  window.history.replaceState(null, '', '/studio/')
  localStorage.setItem(
    'ov_console_connection',
    JSON.stringify({
      baseUrl: 'https://previous-ov.example',
      adminApiKey: 'previous-root-key',
      apiKey: 'previous-user-key',
      accountId: 'old',
      userId: 'old',
    }),
  )
  health.mockResolvedValue({
    service: 'openviking-gateway',
    auth_mode: 'api_key',
    openviking_url: 'https://ov.example',
  })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  function State() {
    const { connection, isGatewayHost, serverMode } = useAppConnection()
    return (
      <output>
        {JSON.stringify({ connection, isGatewayHost, serverMode })}
      </output>
    )
  }
  render(
    <QueryClientProvider client={queryClient}>
      <AppConnectionProvider>
        <State />
      </AppConnectionProvider>
    </QueryClientProvider>,
  )
  await waitFor(() =>
    expect(screen.getByRole('status').textContent).toContain(
      '"serverMode":"api_key"',
    ),
  )
  const state = JSON.parse(screen.getByRole('status').textContent || '{}')
  expect(state.isGatewayHost).toBe(true)
  expect(state.connection).toEqual({
    baseUrl: window.location.origin,
    serviceSelection: 'opensource',
    accountId: 'default',
    userId: 'default',
    apiKey: '',
    adminApiKey: '',
  })
  expect(
    health.mock.calls.every(
      ([baseUrl, headers]) =>
        baseUrl === window.location.origin && !headers?.['X-API-Key'],
    ),
  ).toBe(true)
  queryClient.clear()
})

// Persistence is tested separately with real Web Crypto in credential-storage.test.ts.
vi.mock('#/features/connection/lib/credential-storage', () => ({
  readCredentialStorage: (key: string) => localStorage.getItem(key),
  writeCredentialStorage: (key: string, value: string) =>
    localStorage.setItem(key, value),
  flushCredentialStorage: async () => {},
}))

it('does not discover or replace a hosted connection when the deployment disables gateway discovery', async () => {
  const endpoint = 'https://cloud.example/ov'
  window.__OV_STUDIO_CONFIG__ = {
    edition: 'volcengine',
    hostedApiBaseUrl: endpoint,
  }
  window.history.replaceState(null, '', '/studio/filesystem')
  localStorage.setItem(
    'ov_console_connection',
    JSON.stringify({
      baseUrl: endpoint,
      serviceSelection: 'volcengine',
      apiKey: 'synthetic-user',
      adminApiKey: '',
      accountId: '',
      userId: '',
    }),
  )
  health.mockImplementation(async (url: string) =>
    url === window.location.origin
      ? { service: 'openviking-gateway', auth_mode: 'api_key' }
      : { auth_mode: 'api_key', role: 'user', version: 'test' },
  )
  let state!: ReturnType<typeof useAppConnection>
  function Capture() {
    state = useAppConnection()
    return null
  }
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const view = render(
    <QueryClientProvider client={client}>
      <AppConnectionProvider>
        <Capture />
      </AppConnectionProvider>
    </QueryClientProvider>,
  )
  try {
    await waitFor(() => expect(state.serverMode).toBe('api_key'))
    expect(state.connection.baseUrl).toBe(endpoint)
    expect(state.connection.apiKey).toBe('synthetic-user')
    expect(state.isGatewayHost).toBe(false)
    expect(health.mock.calls.every(([url]) => url === endpoint)).toBe(true)
    expect(() =>
      state.saveConnection({
        ...state.connection,
        baseUrl: window.location.origin,
        serviceSelection: 'opensource',
      }),
    ).toThrow()
    expect(state.connection.baseUrl).toBe(endpoint)
  } finally {
    view.unmount()
    client.clear()
  }
})
