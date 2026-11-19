// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { HomePage } from './page'

const state = vi.hoisted(() => ({
  role: 'user',
  apiKey: 'user-key',
  summary: vi.fn(),
  tokens: vi.fn(),
  commits: vi.fn(),
}))

vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      accountId: 'workspace',
      baseUrl: 'http://service.test',
      userId: 'alice',
      apiKey: state.apiKey,
      adminApiKey: 'control-key',
    },
    connectionRole: state.role,
    serverMode: 'api_key',
  }),
}))
vi.mock('#/features/connection/hooks/use-admin-space-headers', () => ({
  useAdminSpaceHeaders: () => ({
    scopeKey: 'alice',
    isLoading: false,
  }),
}))
vi.mock('#/features/workbench/home/lib/api', () => ({
  fetchConsoleDashboardSummary: state.summary,
  fetchConsoleTokenSeries: state.tokens,
  fetchConsoleContextCommits: state.commits,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => (
    <a href={to}>{children}</a>
  ),
}))

beforeEach(() => {
  state.apiKey = 'user-key'
  vi.stubGlobal('matchMedia', () => ({ matches: true }))
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

function renderUsage() {
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <HomePage />
    </QueryClientProvider>,
  )
}

it('loads personal usage through the existing data credential', async () => {
  state.role = 'user'
  state.summary.mockResolvedValue({ context_counts: { total: 19 } })
  state.tokens.mockResolvedValue({ items: [] })
  state.commits.mockResolvedValue({ items: [] })
  renderUsage()

  expect(screen.getByRole('heading', { name: 'myUsage' })).toBeTruthy()
  expect(await screen.findByText('19')).toBeTruthy()
  await waitFor(() => {
    expect(state.summary).toHaveBeenCalledWith(undefined)
    expect(state.tokens).toHaveBeenCalledWith(undefined)
    expect(state.commits).toHaveBeenCalledWith(undefined)
  })
})

it('loads workspace usage with the current account admin data credential', async () => {
  state.role = 'admin'
  state.apiKey = 'account-admin-key'
  state.summary.mockResolvedValue({ context_counts: { total: 42 } })
  state.tokens.mockResolvedValue({ items: [] })
  state.commits.mockResolvedValue({ items: [] })
  renderUsage()

  expect(screen.getByRole('heading', { name: 'workspaceUsage' })).toBeTruthy()
  expect(await screen.findByText('42')).toBeTruthy()
  expect(state.summary).toHaveBeenCalledWith(undefined)
  expect(state.tokens).toHaveBeenCalledWith(undefined)
  expect(state.commits).toHaveBeenCalledWith(undefined)
})

it.each([
  ['admin', ''],
  ['root', 'root-key'],
])(
  'links %s without an account admin data credential to the admin dashboard',
  (role, apiKey) => {
    state.role = role
    state.apiKey = apiKey
    renderUsage()

    expect(screen.getByText('personalUsageManagementScope')).toBeTruthy()
    expect(
      screen
        .getByRole('link', { name: 'viewWorkspaceUsage' })
        .getAttribute('href'),
    ).toBe('/admin/dashboard')
    expect(state.summary).not.toHaveBeenCalled()
    expect(state.tokens).not.toHaveBeenCalled()
    expect(state.commits).not.toHaveBeenCalled()
  },
)
