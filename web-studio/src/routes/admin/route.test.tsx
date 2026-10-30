// @vitest-environment jsdom
import { Route } from '#/routes/admin/route'
import { cleanup, render, screen } from '@testing-library/react'
import type { ComponentType } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  canManage: false,
  loading: false,
  pathname: '/admin/users',
  gatewayHost: false,
}))
vi.mock('#/features/connection/hooks/use-studio-mode', () => ({
  useStudioMode: () => state,
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    isConnectionRoleLoading: state.loading,
    isGatewayHost: state.gatewayHost,
  }),
}))
vi.mock('@tanstack/react-router', async (original) => ({
  ...(await original<object>()),
  useRouterState: ({
    select,
  }: {
    select: (s: { location: { pathname: string } }) => unknown
  }) => select({ location: { pathname: state.pathname } }),
  Outlet: () => <output data-testid="management-page" />,
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(() => {
  cleanup()
  state.canManage = false
  state.loading = false
  state.pathname = '/admin/users'
  state.gatewayHost = false
})
async function mount() {
  const Admin = Route.options.component as ComponentType & {
    preload?: () => Promise<unknown>
  }
  await Admin.preload?.()
  render(<Admin />)
}
it.each([
  '/admin/users',
  '/admin/configuration',
  '/admin/gateway',
  '/admin/gateway/upstreams/new',
])(
  'blocks direct management URL %s without mounting its effects',
  async (pathname) => {
    state.pathname = pathname
    await mount()
    expect(screen.queryByTestId('management-page')).toBeNull()
    expect(screen.getByRole('heading').textContent).toBe('modes.unavailable')
  },
)
it('waits for server role validation before mounting management pages', async () => {
  state.loading = true
  await mount()
  expect(screen.getByRole('status')).toBeTruthy()
  expect(screen.queryByTestId('management-page')).toBeNull()
})
it('mounts the management subtree only for an authorized identity', async () => {
  state.canManage = true
  await mount()
  expect(screen.getByTestId('management-page')).toBeTruthy()
})

it.each([
  '/admin/settings',
  '/admin/connections/new',
  '/admin/connections/saved-id',
])(
  'keeps local connection page %s available before authorization completes',
  async (pathname) => {
    state.pathname = pathname
    state.loading = true
    await mount()
    expect(screen.getByTestId('management-page')).toBeTruthy()
  },
)

it('preserves the standalone gateway authorization flow without granting user administration', async () => {
  state.gatewayHost = true
  state.pathname = '/admin/gateway/keys'
  await mount()
  expect(screen.getByTestId('management-page')).toBeTruthy()
})
