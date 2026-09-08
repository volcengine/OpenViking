// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Route } from './route'
import { OvClientError } from '#/lib/ov-client/errors'

const state = vi.hoisted(() => ({
  role: 'admin',
  dashboard: vi.fn(),
  tokens: vi.fn(),
  commits: vi.fn(),
}))

vi.mock('./-lib/api', () => ({
  fetchConsoleDashboardSummary: state.dashboard,
  fetchConsoleTokenSeries: state.tokens,
  fetchConsoleContextCommits: state.commits,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => (key === 'requestFailed' ? '请求失败' : key),
  }),
}))

vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: { baseUrl: 'http://localhost:1933', accountId: 'default' },
    connectionRole: state.role,
    isConnectionRoleLoading: false,
  }),
}))

let queryClient: QueryClient

async function renderPage() {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const HomePage = Route.options.component!
  let view!: ReturnType<typeof render>
  await act(async () => {
    view = render(
      <QueryClientProvider client={queryClient}>
        <HomePage />
      </QueryClientProvider>,
    )
  })
  return view
}

beforeEach(() => {
  vi.clearAllMocks()
  state.role = 'admin'
  vi.stubGlobal('matchMedia', () => ({ matches: true }))
  state.dashboard.mockRejectedValue(
    new OvClientError({ code: 'UNAUTHENTICATED', message: 'Missing API key.' }),
  )
  state.tokens.mockRejectedValue(
    new Error('<script>network unavailable</script>'),
  )
  state.commits.mockRejectedValue(new Error('Service unavailable'))
})

afterEach(() => {
  cleanup()
  queryClient.clear()
  vi.unstubAllGlobals()
})

describe('dashboard query errors', () => {
  it('shows each query detail beneath the localized summary as plain text', async () => {
    const { container } = await renderPage()
    expect(await screen.findAllByText('请求失败')).toHaveLength(5)
    expect(
      screen.getAllByText(
        'Missing API key. Please go to Settings and set X-API-Key.',
      ),
    ).toHaveLength(3)
    expect(
      screen.getByText('<script>network unavailable</script>'),
    ).toBeTruthy()
    expect(screen.getByText('Service unavailable')).toBeTruthy()
    expect(container.querySelector('script')).toBeNull()
  })

  it('retains the localized summary when errors have no useful message', async () => {
    state.dashboard.mockRejectedValue(new Error('   '))
    state.tokens.mockRejectedValue(new Error(''))
    state.commits.mockRejectedValue(new Error(''))
    await renderPage()
    expect(await screen.findAllByText('请求失败')).toHaveLength(5)
  })

  it('keeps the usage-disabled state when metrics are turned off', async () => {
    state.dashboard.mockResolvedValue({ enabled: false })
    state.tokens.mockResolvedValue({ enabled: false })
    state.commits.mockResolvedValue({ enabled: false })
    await renderPage()
    expect(await screen.findAllByText('usageDisabled')).toHaveLength(5)
    expect(screen.queryByText('请求失败')).toBeNull()
  })

  it('keeps the access-required state and does not query without a known role', async () => {
    state.role = 'unknown'
    await renderPage()
    expect(screen.getAllByText('usageAccessRequired')).toHaveLength(5)
    expect(screen.queryByText('请求失败')).toBeNull()
    expect(state.dashboard).not.toHaveBeenCalled()
    expect(state.tokens).not.toHaveBeenCalled()
    expect(state.commits).not.toHaveBeenCalled()
  })
})
