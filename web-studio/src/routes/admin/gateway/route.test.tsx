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

import {
  GatewayLayout,
  gatewayTabFor,
} from '#/features/admin/gateway/components/gateway-layout'
import type * as Api from '#/features/admin/gateway/lib/api'
import { GatewayError } from '#/features/admin/gateway/lib/api'

const state = vi.hoisted(() => ({
  role: 'admin',
  roleLoading: false,
  serverMode: 'api_key',
  credential: 'admin-key',
}))
const api = vi.hoisted(() => ({
  getConnectionInfo: vi.fn(),
  getAccess: vi.fn(),
}))

vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({ ready: true, provider: 'opensource' }),
}))

vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'http://localhost:1933',
      accountId: 'acme',
      userId: 'admin',
      apiKey: '',
      adminApiKey: state.credential,
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
vi.mock('#/features/admin/gateway/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof Api>()),
  getConnectionInfo: api.getConnectionInfo,
  getAccess: api.getAccess,
}))

describe('routes', () => {
  it.each([
    ['/admin/gateway', '/admin/gateway/'],
    ['/admin/gateway/upstreams', '/admin/gateway/upstreams/'],
    ['/admin/gateway/upstreams/new', '/admin/gateway/upstreams/$upstreamId'],
    ['/admin/gateway/profiles', '/admin/gateway/profiles/'],
    ['/admin/gateway/profiles/p1', '/admin/gateway/profiles/$profileId'],
    ['/admin/gateway/keys', '/admin/gateway/keys'],
    ['/admin/gateway/requests', '/admin/gateway/requests'],
    ['/admin/gateway/connect', '/admin/gateway/connect'],
  ])('matches %s', (path, routeId) => {
    const router = createRouter({
      routeTree,
      history: createMemoryHistory({ initialEntries: [path] }),
    })
    expect(router.matchRoutes(path).at(-1)?.routeId).toBe(routeId)
  })

  it.each([
    ['/admin/gateway', 'overview'],
    ['/admin/gateway/', 'overview'],
    ['/admin/gateway/upstreams/new', 'upstreams'],
    ['/admin/gateway/profiles/p1', 'profiles'],
    ['/admin/gateway/requests', 'requests'],
    ['/admin/gateway/unknown', 'overview'],
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
    api.getConnectionInfo.mockResolvedValue({
      base_url: 'https://gateway.example.com',
      public_url_configured: true,
    })
    state.credential = 'admin-key'
    api.getAccess.mockReset()
    api.getAccess.mockResolvedValue({
      managed: false,
      can_write_objects: true,
      can_import_vlm: true,
      can_issue_for_others: true,
      role: 'admin',
      account_id: 'acme',
      user_id: 'admin',
      warnings: [],
    })
  })
  afterEach(cleanup)

  function renderLayout() {
    const root = createRootRoute({ component: Outlet })
    const router = createRouter({
      routeTree: root.addChildren([
        createRoute({
          getParentRoute: () => root,
          path: '/admin/gateway',
          component: GatewayLayout,
        }),
        createRoute({ getParentRoute: () => root, path: '/settings' }),
      ]),
      history: createMemoryHistory({ initialEntries: ['/admin/gateway'] }),
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

  it('asks for an OpenViking key if none is configured', async () => {
    state.role = 'user'
    state.credential = ''
    renderLayout()
    expect(await screen.findByText('access.title')).toBeTruthy()
    expect(screen.queryByText('tabs.overview')).toBeNull()
    expect(api.getConnectionInfo).not.toHaveBeenCalled()
  })

  it('explains how to turn the gateway on in development mode', async () => {
    state.serverMode = 'dev'
    state.role = 'root'
    state.credential = ''
    api.getAccess.mockRejectedValue(
      new GatewayError(
        'OpenViking Gateway is not enabled',
        404,
        undefined,
        'not_enabled',
      ),
    )
    renderLayout()
    expect(await screen.findByText('unavailable.notEnabled.title')).toBeTruthy()
    expect(screen.getByText(/"enabled":true/)).toBeTruthy()
    expect(screen.queryByText('tabs.overview')).toBeNull()
  })

  it('asks an account admin to contact the deployer', async () => {
    api.getAccess.mockRejectedValue(
      new GatewayError('Not Found', 404, undefined, 'not_enabled'),
    )
    renderLayout()
    expect(await screen.findByText('unavailable.contact')).toBeTruthy()
    expect(screen.queryByText(/"enabled":true/)).toBeNull()
  })

  it('shows gateway pages in development mode without credentials', async () => {
    state.serverMode = 'dev'
    state.role = 'root'
    state.credential = ''
    renderLayout()
    expect(await screen.findByText('tabs.overview')).toBeTruthy()
    expect(screen.queryByText('access.title')).toBeNull()
  })

  it.each([
    [
      new GatewayError('trusted', 409, undefined, 'trusted_mode_unsupported'),
      'unavailable.trusted.title',
    ],
    [
      new GatewayError('Not Found', 404, undefined, 'not_enabled'),
      'unavailable.notEnabled.title',
    ],
  ])('shows a setup card for %s', async (error, title) => {
    api.getAccess.mockRejectedValue(error)
    renderLayout()
    expect(await screen.findByText(title)).toBeTruthy()
    expect(screen.getByText('actions.retry')).toBeTruthy()
  })

  it('shows an unreachable OpenViking on an embedded server as a plain failure', async () => {
    api.getAccess.mockRejectedValue(
      new GatewayError('unreachable', 503, undefined, 'openviking_unreachable'),
    )
    renderLayout()
    expect(await screen.findByText('unavailable.failed.title')).toBeTruthy()
    expect(
      screen.queryByText('unavailable.openvikingUnreachable.title'),
    ).toBeNull()
    expect(screen.getByText('actions.retry')).toBeTruthy()
  })

  it('shows other failures as an error with retry', async () => {
    api.getAccess.mockRejectedValue(new GatewayError('Network Error'))
    renderLayout()
    expect(await screen.findByText('unavailable.failed.title')).toBeTruthy()
    expect(screen.getByText('Network Error')).toBeTruthy()
    expect(screen.getByText('actions.retry')).toBeTruthy()
  })

  it('shows the gateway address and the tabs once the gateway answers', async () => {
    api.getConnectionInfo.mockResolvedValue({
      base_url: 'https://gw.example.com/gateway',
      public_url_configured: true,
    })
    renderLayout()
    expect(
      await screen.findByText('https://gw.example.com/gateway'),
    ).toBeTruthy()
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
