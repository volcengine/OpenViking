// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { cleanup, render, screen } from '@testing-library/react'
import type * as ReactI18next from 'react-i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { routeTree } from '#/routeTree.gen'

import { GatewayError } from './-lib/api'
import type * as Api from './-lib/api'
import {
  ContextGatewayLayout,
  gatewayTabFor,
} from './-components/gateway-layout'

const state = vi.hoisted(() => ({
  role: 'admin',
  roleLoading: false,
  serverMode: 'api_key',
}))
const api = vi.hoisted(() => ({ getConnectionInfo: vi.fn() }))

vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'http://localhost:1933',
      accountId: 'acme',
      userId: 'admin',
      apiKey: '',
      adminApiKey: 'admin-key',
    },
    connectionRole: state.role,
    isConnectionRoleLoading: state.roleLoading,
    serverMode: state.serverMode,
  }),
}))
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof ReactI18next>()),
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: 'en' },
  }),
}))
vi.mock('./-lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof Api>()),
  getConnectionInfo: api.getConnectionInfo,
}))

describe('routes', () => {
  it.each([
    ['/context-gateway', '/context-gateway/'],
    ['/context-gateway/upstreams', '/context-gateway/upstreams/'],
    [
      '/context-gateway/upstreams/new',
      '/context-gateway/upstreams/$upstreamId',
    ],
    ['/context-gateway/profiles', '/context-gateway/profiles/'],
    ['/context-gateway/profiles/p1', '/context-gateway/profiles/$profileId'],
    ['/context-gateway/keys', '/context-gateway/keys'],
    ['/context-gateway/requests', '/context-gateway/requests'],
    ['/context-gateway/connect', '/context-gateway/connect'],
  ])('matches %s', (path, routeId) => {
    const router = createRouter({
      routeTree,
      history: createMemoryHistory({ initialEntries: [path] }),
    })
    expect(router.matchRoutes(path).at(-1)?.routeId).toBe(routeId)
  })

  it.each([
    ['/context-gateway', 'overview'],
    ['/context-gateway/', 'overview'],
    ['/context-gateway/upstreams/new', 'upstreams'],
    ['/context-gateway/profiles/p1', 'profiles'],
    ['/context-gateway/requests', 'requests'],
    ['/context-gateway/unknown', 'overview'],
  ])('puts %s under the %s tab', (path, tab) => {
    expect(gatewayTabFor(path)).toBe(tab)
  })
})

describe('layout gates', () => {
  beforeEach(() => {
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    state.role = 'admin'
    state.roleLoading = false
    state.serverMode = 'api_key'
    api.getConnectionInfo.mockReset()
  })
  afterEach(cleanup)

  function renderLayout() {
    const root = createRootRoute({ component: Outlet })
    const router = createRouter({
      routeTree: root.addChildren([
        createRoute({
          getParentRoute: () => root,
          path: '/context-gateway',
          component: ContextGatewayLayout,
        }),
        createRoute({ getParentRoute: () => root, path: '/settings' }),
      ]),
      history: createMemoryHistory({ initialEntries: ['/context-gateway'] }),
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )
  }

  it('asks for an account administrator key', async () => {
    state.role = 'user'
    renderLayout()
    expect(await screen.findByText('access.title')).toBeTruthy()
    expect(screen.queryByText('tabs.overview')).toBeNull()
    expect(api.getConnectionInfo).not.toHaveBeenCalled()
  })

  it('explains how to turn the gateway on', async () => {
    api.getConnectionInfo.mockRejectedValue(
      new GatewayError('Context Gateway is not enabled', 503),
    )
    renderLayout()
    expect(await screen.findByText('unavailable.notEnabled.title')).toBeTruthy()
    expect(screen.getByText(/"enabled": true/)).toBeTruthy()
    expect(screen.getByText('unavailable.docs')).toBeTruthy()
    expect(screen.queryByText('tabs.overview')).toBeNull()
  })

  it('explains development mode instead of asking for a key', async () => {
    state.serverMode = 'dev'
    state.role = 'root'
    renderLayout()
    expect(await screen.findByText('unavailable.devMode.title')).toBeTruthy()
    expect(screen.getByText(/"auth_mode": "api_key"/)).toBeTruthy()
    expect(screen.queryByText('access.title')).toBeNull()
    expect(api.getConnectionInfo).not.toHaveBeenCalled()
  })

  it.each([
    [
      new GatewayError('Invalid gateway management credential', 401),
      'unavailable.tokenMismatch.title',
    ],
    [
      new GatewayError('Not Found', 404, undefined, 'unsupported'),
      'unavailable.unsupported.title',
    ],
  ])('shows a setup card for %s', async (error, title) => {
    api.getConnectionInfo.mockRejectedValue(error)
    renderLayout()
    expect(await screen.findByText(title)).toBeTruthy()
    expect(screen.getByText('actions.retry')).toBeTruthy()
  })

  it('shows other failures as an error with retry', async () => {
    api.getConnectionInfo.mockRejectedValue(new GatewayError('Network Error'))
    renderLayout()
    expect(await screen.findByText('unavailable.failed.title')).toBeTruthy()
    expect(screen.getByText('Network Error')).toBeTruthy()
    expect(screen.getByText('actions.retry')).toBeTruthy()
  })

  it('shows the gateway address and the tabs once the gateway answers', async () => {
    api.getConnectionInfo.mockResolvedValue({
      base_url: 'https://gw.example.com',
      public_url_configured: true,
    })
    renderLayout()
    expect(await screen.findByText('https://gw.example.com')).toBeTruthy()
    expect(
      screen.getByRole('button', {
        name: 'beta.label',
        description: 'beta.hint',
      }),
    ).toBeTruthy()
    for (const tab of [
      'overview',
      'upstreams',
      'profiles',
      'keys',
      'requests',
      'connect',
    ]) {
      expect(screen.getByText(`tabs.${tab}`)).toBeTruthy()
    }
  })
})
