// @vitest-environment jsdom
import type { ComponentType } from 'react'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import userEvent from '@testing-library/user-event'

import { Route } from './route'

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
vi.mock('#/lib/admin', () => ({ probeStudioConnection: probe }))
vi.mock('#/hooks/use-app-connection', () => ({
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
vi.mock('#/hooks/use-studio-service', () => ({
  useStudioService: () => ({
    provider: serviceState.provider,
    ready: serviceState.ready,
    version: '',
    isChecking: false,
    health: { refetch: vi.fn() },
  }),
}))
vi.mock('./-components/server-configuration', () => ({
  ServerConfigurationEditor: () => <span data-testid="configuration-editor" />,
}))
afterEach(() => {
  cleanup()
  healthState.value = null
  saveConnection.mockClear()
  reconnect.mockClear()
})

it('preserves the active connection when an invalid address is saved from the details panel', async () => {
  probe.mockResolvedValue({
    admin: { state: 'skipped' },
    data: { state: 'ok' },
  })
  const client = new QueryClient()
  const Settings = Route.options.component as ComponentType & {
    preload?: () => Promise<unknown>
  }
  await Settings.preload?.()
  render(
    <QueryClientProvider client={client}>
      <Settings />
    </QueryClientProvider>,
  )
  fireEvent.change(document.getElementById('settings-base-url')!, {
    target: { value: 'not-a-url' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'studio:profiles.save' }))
  expect(screen.getByRole('alert').textContent).toBe(
    'studio:profiles.invalidUrl',
  )
  expect(saveConnection).not.toHaveBeenCalled()
  expect(reconnect).not.toHaveBeenCalled()
  fireEvent.change(document.getElementById('settings-base-url')!, {
    target: { value: 'https://proxy.example/openviking' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'studio:profiles.save' }))
  expect(screen.queryByRole('alert')).toBeNull()
  expect(saveConnection).toHaveBeenCalledWith(
    expect.objectContaining({ baseUrl: 'https://proxy.example/openviking' }),
  )
  expect(reconnect).toHaveBeenCalledTimes(1)
  client.clear()
})

it('uses the hosted default address when the Base UI service selector changes', async () => {
  const user = userEvent.setup()
  const client = new QueryClient()
  const Settings = Route.options.component as ComponentType
  render(
    <QueryClientProvider client={client}>
      <Settings />
    </QueryClientProvider>,
  )
  const serviceType = screen.getByRole('combobox', {
    name: 'studio:serviceType',
  })
  const url = screen.getByLabelText<HTMLInputElement>('fields.baseUrl')
  fireEvent.change(url, { target: { value: 'https://open-source.example/ov' } })
  await user.click(serviceType)
  await user.click(
    screen.getByRole('option', { name: 'studio:providers.volcengine' }),
  )
  expect(url.value).toBe(
    'https://api.vikingdb.cn-beijing.volces.com/openviking',
  )
  expect(url.readOnly).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'studio:profiles.save' }))
  expect(saveConnection).toHaveBeenCalledWith(
    expect.objectContaining({
      serviceSelection: 'volcengine',
      baseUrl: 'https://api.vikingdb.cn-beijing.volces.com/openviking',
    }),
  )
  client.clear()
})

it('shows only the server-resolved identity next to the data key', async () => {
  probe.mockResolvedValue({
    admin: { state: 'skipped' },
    data: { state: 'ok' },
  })
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
  expect(
    await screen.findByText('[studio:profiles.identityPending]'),
  ).toBeTruthy()
  expect(screen.queryByText('[account-a/alice]')).toBeNull()
  healthState.value = { account_id: 'server-account', user_id: 'server-user' }
  view.rerender(content())
  expect(await screen.findByText('[server-account/server-user]')).toBeTruthy()
  fireEvent.change(document.getElementById('settings-user-api-key')!, {
    target: { value: 'unverified-key' },
  })
  expect(screen.getByText('[studio:profiles.identityPending]')).toBeTruthy()
  client.clear()
})

it.each([false, undefined, true])(
  'requires the Root key in trusted mode regardless of legacy metadata: %s',
  async (required) => {
    probe.mockResolvedValue({
      admin: {
        state: 'error',
        statusCode: 403,
        detail: 'Management unavailable',
      },
      data: { state: 'ok', detailCode: 'tenantDataAvailable' },
      rootApiKeyRequired: required,
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const Settings = Route.options.component as ComponentType & {
      preload?: () => Promise<unknown>
    }
    await Settings.preload?.()
    render(
      <QueryClientProvider client={client}>
        <Settings />
      </QueryClientProvider>,
    )
    await waitFor(
      () => expect(screen.getByText('Management unavailable')).toBeTruthy(),
      { timeout: 5000 },
    )
    expect(
      Boolean(screen.queryByText('connection.keyGuide.trusted.title')),
    ).toBe(true)
    client.clear()
  },
)

it('keeps management credentials editable after authentication fails and retries the connection owner', async () => {
  serviceState.provider = 'unknown'
  config.url = 'https://custom.example'
  config.selection = 'opensource'
  probe.mockResolvedValue({
    admin: { state: 'skipped' },
    data: { state: 'skipped' },
  })
  const client = new QueryClient()
  const Settings = Route.options.component as ComponentType
  render(
    <QueryClientProvider client={client}>
      <Settings />
    </QueryClientProvider>,
  )
  expect(document.getElementById('settings-root-api-key')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'studio:retry' }))
  expect(reconnect).toHaveBeenCalledTimes(1)
  client.clear()
  serviceState.provider = 'opensource'
  config.url = 'http://localhost:1933'
  config.selection = 'auto'
})

it('unmounts the configuration editor and resets the active tab when switching to a hosted connection', async () => {
  Object.assign(serviceState, {
    provider: 'opensource',
    ready: true,
    role: 'root',
    key: 'root-key',
  })
  probe.mockResolvedValue({ admin: { state: 'ok' }, data: { state: 'ok' } })
  const client = new QueryClient()
  const Settings = Route.options.component as ComponentType
  const content = () => (
    <QueryClientProvider client={client}>
      <Settings />
    </QueryClientProvider>
  )
  const view = render(content())
  fireEvent.click(screen.getByRole('tab', { name: 'tabs.configuration' }))
  expect(await screen.findByTestId('configuration-editor')).toBeTruthy()
  serviceState.provider = 'volcengine'
  view.rerender(content())
  expect(screen.queryByRole('tab', { name: 'tabs.configuration' })).toBeNull()
  expect(screen.queryByTestId('configuration-editor')).toBeNull()
  expect(
    screen
      .getByRole('tab', { name: 'tabs.connection' })
      .getAttribute('aria-selected'),
  ).toBe('true')
  client.clear()
  Object.assign(serviceState, { provider: 'opensource', role: 'user', key: '' })
})

it('preserves an edited URL and user key while health fills in identity', async () => {
  const originalIdentity = {
    accountId: connectionState.accountId,
    userId: connectionState.userId,
  }
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const Settings = Route.options.component as ComponentType
  const content = () => (
    <QueryClientProvider client={client}>
      <Settings />
    </QueryClientProvider>
  )
  const view = render(content())
  const url = screen.getByLabelText<HTMLInputElement>('fields.baseUrl')
  const key = document.getElementById(
    'settings-user-api-key',
  ) as HTMLInputElement
  fireEvent.change(url, { target: { value: 'http://recovery.test' } })
  fireEvent.change(key, { target: { value: 'synthetic-draft' } })
  connectionState.accountId = 'resolved-account'
  connectionState.userId = 'resolved-user'
  view.rerender(content())
  expect(url.value).toBe('http://recovery.test')
  expect(key.value).toBe('synthetic-draft')
  fireEvent.click(screen.getByRole('button', { name: 'studio:profiles.save' }))
  expect(saveConnection).toHaveBeenCalledWith(
    expect.objectContaining({
      baseUrl: 'http://recovery.test',
      apiKey: 'synthetic-draft',
      accountId: 'resolved-account',
      userId: 'resolved-user',
    }),
  )
  Object.assign(connectionState, originalIdentity)
  client.clear()
})
