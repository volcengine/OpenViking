// @vitest-environment jsdom
import type * as ConnectionHealth from '#/features/connection/lib/health'
import { PermissionsPage } from '#/features/resource-acl/components/permissions-page'
import '#/i18n'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  apiKey: '',
  hasAdminKey: false,
  createApi: vi.fn(),
  listDirectory: vi.fn(async () => []),
  getEnabled: vi.fn(async () => true),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'http://service.test',
      accountId: 'default',
      userId: 'default',
      adminApiKey: 'root-key',
      apiKey: state.apiKey,
    },
    connectionRole: 'root',
    serverMode: 'api_key',
    studioMode: 'management',
    isConnectionRoleLoading: false,
    identityScopeKey: `root:${state.apiKey}`,
    switchIdentity: vi.fn(),
  }),
}))
vi.mock('#/features/resource-acl/lib/resource-acl', async (original) => ({
  ...(await original<object>()),
  createResourceAclApi: (...args: unknown[]) => {
    state.createApi(...args)
    return { getEnabled: state.getEnabled, listDirectory: state.listDirectory }
  },
}))
vi.mock('#/shared/api/admin', async (original) => ({
  ...(await original<object>()),
  fetchAdminUsers: async () =>
    state.hasAdminKey
      ? [
          {
            userId: 'admin',
            accountId: 'default',
            role: 'admin',
            apiKey: 'data-key',
          },
        ]
      : [],
}))
vi.mock('#/features/connection/lib/health', async (importOriginal) => ({
  ...(await importOriginal<typeof ConnectionHealth>()),
  fetchServerHealth: async () => ({
    role: 'admin',
    account_id: 'default',
    user_id: 'admin',
  }),
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}))
afterEach(() => {
  cleanup()
  state.apiKey = ''
  state.hasAdminKey = false
  state.createApi.mockClear()
  state.listDirectory.mockClear()
})
it('does not request tenant directories with only a Root key and loads them after selecting a data identity', async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const content = () => (
    <QueryClientProvider client={client}>
      <PermissionsPage />
    </QueryClientProvider>
  )
  const view = render(content())
  await screen.findByRole('heading', {
    name: 'Select an account administrator identity',
  })
  expect(state.listDirectory).not.toHaveBeenCalled()
  await waitFor(() => expect(state.getEnabled).toHaveBeenCalled())
  state.apiKey = 'user-key'
  view.rerender(content())
  await waitFor(() =>
    expect(state.listDirectory).toHaveBeenCalledWith('viking://resources/'),
  )
  expect(
    screen.queryByRole('heading', {
      name: 'Select an account administrator identity',
    }),
  ).toBeNull()
  client.clear()
})

it('automatically uses the account Admin key for directory management under Root', async () => {
  state.hasAdminKey = true
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const view = render(
    <QueryClientProvider client={client}>
      <PermissionsPage />
    </QueryClientProvider>,
  )
  await waitFor(() =>
    expect(state.listDirectory).toHaveBeenCalledWith('viking://resources/'),
  )
  expect(state.createApi).toHaveBeenCalledWith(
    expect.objectContaining({ adminApiKey: 'data-key' }),
    false,
    true,
  )
  expect(state.apiKey).toBe('')
  view.unmount()
  client.clear()
})
