// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { IdentityScopedAppShell } from './connection-boundary'
import { AdminRoute } from '#/app/router/admin-boundary'
import { useStudioMode } from '#/features/connection/hooks/use-studio-mode'

const state = vi.hoisted(() => ({
  pathname: '/filesystem',
  role: 'user',
  mode: 'workbench',
  apiKey: 'user-key',
  adminApiKey: '',
  edition: 'self-hosted',
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'https://private.example',
      accountId: 'team',
      userId: 'alice',
      apiKey: state.apiKey,
      adminApiKey: state.adminApiKey,
    },
    connectionRole: state.role,
    isConnectionRoleLoading: false,
    isGatewayHost: false,
    serverMode: 'api_key',
    studioMode: state.mode,
  }),
}))
vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({ provider: 'opensource' }),
}))
vi.mock('./studio-layout', () => ({
  StudioLayout: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
}))
vi.mock('./studio-feature-boundary', () => ({
  StudioFeatureBoundary: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
}))
vi.mock('@tanstack/react-router', () => ({
  Navigate: ({ to }: { to: string }) => <output>{to}</output>,
  Link: ({ children }: { children: React.ReactNode }) => (
    <span>{children}</span>
  ),
  Outlet: () => <div data-testid="admin-content" />,
  useRouterState: () => state.pathname,
}))
afterEach(() => {
  cleanup()
  Object.assign(state, {
    pathname: '/filesystem',
    role: 'user',
    mode: 'workbench',
    apiKey: 'user-key',
    adminApiKey: '',
  })
  delete window.__OV_STUDIO_CONFIG__
})
it('redirects control-only ROOT deep links before mounting personal data components', () => {
  state.apiKey = ''
  state.adminApiKey = 'root-key'
  state.role = 'unknown'
  render(
    <IdentityScopedAppShell>
      <div data-testid="private-data" />
    </IdentityScopedAppShell>,
  )
  expect(screen.getByText('/admin')).toBeTruthy()
  expect(screen.queryByTestId('private-data')).toBeNull()
})
it.each(['admin', 'user'])(
  'keeps the %s data identity in its own workspace',
  (role) => {
    state.role = role
    render(
      <IdentityScopedAppShell>
        <div data-testid="private-data" />
      </IdentityScopedAppShell>,
    )
    expect(screen.getByTestId('private-data')).toBeTruthy()
  },
)
it.each([
  ['admin', true],
  ['user', false],
] as const)('allows management for %s: %s', (role, allowed) => {
  state.role = role
  state.adminApiKey = role === 'admin' ? 'admin-key' : 'saved-root-key'
  function Entry() {
    return <output>{String(useStudioMode().canManage)}</output>
  }
  render(<Entry />)
  expect(screen.getByText(String(allowed))).toBeTruthy()
})
it('rejects an ordinary User management deep link without mounting administration', () => {
  state.pathname = '/admin/users'
  state.mode = 'management'
  render(<AdminRoute />)
  expect(screen.queryByTestId('admin-content')).toBeNull()
})
it('official deployment rejects even management settings deep links', () => {
  window.__OV_STUDIO_CONFIG__ = { edition: 'volcengine' }
  state.pathname = '/admin/settings'
  state.mode = 'management'
  state.role = 'root'
  state.adminApiKey = 'root-key'
  render(<AdminRoute />)
  expect(screen.getByText('/filesystem')).toBeTruthy()
  expect(screen.queryByTestId('admin-content')).toBeNull()
})
