// @vitest-environment jsdom
import { StudioFeatureBoundary } from '#/app/layouts/studio-feature-boundary'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  pathname: '/compile',
  provider: 'volcengine',
  ready: true,
  capability: 'unavailable',
  role: 'admin',
  mode: 'api_key',
  controlKey: 'mock-admin',
}))
vi.mock('@tanstack/react-router', () => ({
  useRouterState: () => state.pathname,
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: { adminApiKey: state.controlKey, apiKey: 'mock-user' },
    connectionRole: state.role,
    isConnectionRoleLoading: false,
    serverMode: state.mode,
    studioMode: state.pathname.startsWith('/admin')
      ? 'management'
      : 'workbench',
  }),
}))
vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({
    provider: state.provider,
    ready: state.ready,
    isChecking: false,
  }),
}))
vi.mock('#/features/connection/hooks/use-studio-capabilities', () => ({
  useStudioCapabilities: () => ({
    compile: { data: state.capability, isPending: false, refetch: vi.fn() },
  }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}))
afterEach(() => {
  cleanup()
  state.provider = 'volcengine'
  state.ready = true
  state.mode = 'api_key'
  state.role = 'admin'
  state.controlKey = 'mock-admin'
})
it.each(['user', 'admin'])(
  'blocks Root-only bot management before child effects for %s',
  (role) => {
    state.provider = 'opensource'
    state.pathname = '/admin/vikingbot'
    state.role = role
    render(
      <StudioFeatureBoundary>
        <span data-testid="bot child" />
      </StudioFeatureBoundary>,
    )
    expect(screen.queryByTestId('bot child')).toBeNull()
    expect(screen.getByText('states.managementRequired.title')).toBeTruthy()
  },
)
it('allows Root bot management and Account Admin experience settings', () => {
  state.provider = 'opensource'
  state.pathname = '/admin/vikingbot'
  state.role = 'root'
  const view = render(
    <StudioFeatureBoundary>
      <span data-testid="management child" />
    </StudioFeatureBoundary>,
  )
  expect(screen.getByTestId('management child')).toBeTruthy()
  state.role = 'admin'
  state.pathname = '/admin/configuration'
  view.rerender(
    <StudioFeatureBoundary>
      <span data-testid="management child" />
    </StudioFeatureBoundary>,
  )
  expect(screen.getByTestId('management child')).toBeTruthy()
})
it('blocks ordinary User access to the OpenViking configuration page', () => {
  state.provider = 'opensource'
  state.role = 'user'
  state.pathname = '/admin/configuration'
  render(
    <StudioFeatureBoundary>
      <span data-testid="configuration child" />
    </StudioFeatureBoundary>,
  )
  expect(screen.queryByTestId('configuration child')).toBeNull()
})
it('does not mount an incompatible extension or its effects through a deep link', () => {
  state.pathname = '/compile'
  state.capability = 'unavailable'
  render(
    <StudioFeatureBoundary>
      <span data-testid="dangerous child" />
    </StudioFeatureBoundary>,
  )
  expect(screen.queryByTestId('dangerous child')).toBeNull()
  expect(screen.getByText('states.unavailable.title')).toBeTruthy()
})
it('does not grant management to a hosted admin data identity', () => {
  state.pathname = '/admin/users/memory-templates'
  render(
    <StudioFeatureBoundary>
      <span data-testid="management child" />
    </StudioFeatureBoundary>,
  )
  expect(screen.queryByTestId('management child')).toBeNull()
  expect(screen.getByText('states.managementRequired.title')).toBeTruthy()
})
it('keeps public directory access independent of extension capability failures', () => {
  state.pathname = '/directory'
  render(
    <StudioFeatureBoundary>
      <span data-testid="directory child" />
    </StudioFeatureBoundary>,
  )
  expect(screen.getByTestId('directory child')).toBeTruthy()
})

it('does not mount personal-directory requests before a custom deployment is identified', () => {
  state.pathname = '/directory'
  state.provider = 'unknown'
  render(
    <StudioFeatureBoundary>
      <span data-testid="directory child" />
    </StudioFeatureBoundary>,
  )
  expect(screen.queryByTestId('directory child')).toBeNull()
  expect(screen.getByText('states.unknownProvider.title')).toBeTruthy()
})

it.each([
  '/admin/gateway',
  '/admin/gateway/upstreams/model',
  '/admin/gateway/keys',
])(
  'blocks hosted Gateway deep link %s before mounting children',
  (pathname) => {
    state.pathname = pathname
    render(
      <StudioFeatureBoundary>
        <span data-testid="gateway-child" />
      </StudioFeatureBoundary>,
    )
    expect(screen.queryByTestId('gateway-child')).toBeNull()
  },
)
it('allows trusted connections to reach Gateway access verification', () => {
  Object.assign(state, {
    pathname: '/admin/gateway',
    provider: 'opensource',
    mode: 'trusted',
  })
  render(
    <StudioFeatureBoundary>
      <span data-testid="gateway-child" />
    </StudioFeatureBoundary>,
  )
  expect(screen.getByTestId('gateway-child')).toBeTruthy()
})

it('allows a configured trusted connection to mount workbench pages', () => {
  state.provider = 'opensource'
  state.mode = 'trusted'
  state.pathname = '/filesystem'
  render(
    <StudioFeatureBoundary>
      <span data-testid="trusted-data-child" />
    </StudioFeatureBoundary>,
  )
  expect(screen.getByTestId('trusted-data-child')).toBeTruthy()
})

it('requires the trusted credential before mounting data pages', () => {
  Object.assign(state, {
    provider: 'opensource',
    mode: 'trusted',
    pathname: '/filesystem',
    controlKey: '',
  })
  render(
    <StudioFeatureBoundary>
      <span data-testid="trusted-data-child" />
    </StudioFeatureBoundary>,
  )
  expect(screen.queryByTestId('trusted-data-child')).toBeNull()
  expect(
    screen.getByText('states.trustedCredentialRequired.title'),
  ).toBeTruthy()
})
