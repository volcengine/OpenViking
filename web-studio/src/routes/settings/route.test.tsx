// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import type { ComponentType } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

import { Route } from '#/routes/settings/route'

const {
  probe,
  reconnect,
  saveConnection,
  serviceState,
  config,
  connectionState,
  healthState,
} = vi.hoisted(() => ({
  connectionState: {
    baseUrl: '',
    serviceSelection: 'auto',
    accountId: 'account-a',
    userId: 'alice',
    adminApiKey: '',
    apiKey: '',
  },
  probe: vi.fn(),
  reconnect: vi.fn(),
  saveConnection: vi.fn(),
  serviceState: {
    provider: 'opensource',
    ready: true,
    role: 'user',
    key: '',
  },
  config: { url: 'http://localhost:1933', selection: 'auto' },
  healthState: { value: null as null | Record<string, unknown> },
}))
vi.mock('#/shared/api/admin', () => ({ probeStudioConnection: probe }))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      ...Object.assign(connectionState, {
        baseUrl: config.url,
        serviceSelection: config.selection,
        adminApiKey: serviceState.key,
      }),
    },
    serverMode: 'trusted',
    serverHealth: healthState.value,
    connectionRole: serviceState.role,
    isConnectionRoleLoading: false,
    identityScopeKey: 'settings-test',
    saveConnection,
    reconnect,
  }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: 'en' },
  }),
}))
vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({
    provider: serviceState.provider,
    ready: serviceState.ready,
    version: '',
    isChecking: false,
    health: { refetch: vi.fn() },
  }),
}))
vi.mock('#/features/connection/components/connection-profiles', () => ({
  ConnectionProfilesList: () => <span data-testid="connection-profiles" />,
}))
vi.mock(
  '#/features/admin/server-configuration/components/server-configuration',
  () => ({
    ServerConfigurationEditor: () => (
      <span data-testid="configuration-editor" />
    ),
  }),
)
afterEach(() => {
  cleanup()
  healthState.value = null
  saveConnection.mockClear()
  reconnect.mockClear()
})

it('keeps service configuration out of connection settings for both providers', async () => {
  Object.assign(serviceState, {
    provider: 'opensource',
    ready: true,
    role: 'root',
    key: 'root-key',
  })
  probe.mockResolvedValue({ admin: { state: 'ok' }, data: { state: 'ok' } })
  const client = new QueryClient()
  const Settings = Route.options.component as ComponentType & {
    preload?: () => Promise<unknown>
  }
  await Settings.preload?.()
  const content = () => (
    <QueryClientProvider client={client}>
      <Settings />
    </QueryClientProvider>
  )
  const view = render(content())
  expect(screen.queryByRole('tab', { name: 'tabs.configuration' })).toBeNull()
  expect(screen.getByTestId('connection-profiles')).toBeTruthy()
  serviceState.provider = 'volcengine'
  view.rerender(content())
  expect(screen.queryByRole('tab', { name: 'tabs.configuration' })).toBeNull()
  expect(screen.queryByTestId('configuration-editor')).toBeNull()
  expect(screen.getByTestId('connection-profiles')).toBeTruthy()
  client.clear()
  Object.assign(serviceState, { provider: 'opensource', role: 'user', key: '' })
})
