// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { StudioFeatureBoundary } from './studio-feature-boundary'

const state = vi.hoisted(() => ({
  pathname: '/compile',
  provider: 'volcengine',
  ready: true,
  capability: 'unavailable',
  role: 'admin',
  mode: 'api_key',
}))
vi.mock('@tanstack/react-router', () => ({
  useRouterState: () => state.pathname,
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: { adminApiKey: 'mock-admin' },
    connectionRole: state.role,
    isConnectionRoleLoading: false,
    serverMode: state.mode,
  }),
}))
vi.mock('#/hooks/use-studio-service', () => ({
  useStudioService: () => ({
    provider: state.provider,
    ready: state.ready,
    isChecking: false,
  }),
}))
vi.mock('#/hooks/use-studio-capabilities', () => ({
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
  state.pathname = '/users/memory-templates'
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

it.each(['/gateway', '/gateway/upstreams/model', '/gateway/keys'])(
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
    pathname: '/gateway',
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
