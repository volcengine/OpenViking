// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AppShell } from '#/app/layouts/app-shell'
import { ConnectionScopedRouteContent } from '#/app/layouts/connection-boundary'
import { shouldShowGatewayNavigation } from '#/app/navigation/visibility'

const bootstrap = vi.hoisted(() => ({
  health: vi.fn(),
  pathname: '/',
  navigate: vi.fn(),
}))
vi.mock('#/features/connection/lib/health', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  fetchServerHealth: bootstrap.health,
}))
vi.mock('#/features/connection/hooks/use-app-connection', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  AppConnectionProvider: () => <span data-testid="connected-provider" />,
}))

vi.mock('@tanstack/react-router', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  Navigate: ({ to }: { to: string }) => <output>{to}</output>,
  useNavigate: () => bootstrap.navigate,
  useRouterState: ({
    select,
  }: {
    select: (state: { location: { pathname: string } }) => unknown
  }) => select({ location: { pathname: bootstrap.pathname } }),
}))

afterEach(() => {
  cleanup()
  bootstrap.pathname = '/'
})

describe('ConnectionScopedRouteContent', () => {
  it('does not mount route content while the server mode is unresolved', () => {
    render(
      <ConnectionScopedRouteContent serverMode="checking">
        <span data-testid="identity-scoped-route" />
      </ConnectionScopedRouteContent>,
    )

    expect(screen.queryByTestId('identity-scoped-route')).toBeNull()
  })

  it.each(['api_key', 'trusted', 'dev', 'oidc', 'ldap', 'offline'] as const)(
    'mounts route content after resolving %s mode',
    (serverMode) => {
      render(
        <ConnectionScopedRouteContent serverMode={serverMode}>
          <span data-testid="identity-scoped-route" />
        </ConnectionScopedRouteContent>,
      )

      expect(screen.getByTestId('identity-scoped-route')).toBeTruthy()
    },
  )
})

it('routes first use to the onboarding before mounting business routes', () => {
  localStorage.clear()
  sessionStorage.clear()
  render(
    <AppShell>
      <span data-testid="business-route" />
    </AppShell>,
  )
  expect(screen.queryByTestId('business-route')).toBeNull()
  expect(screen.getByText('/onboarding')).toBeTruthy()
  expect(localStorage.getItem('ov_console_connection_profiles')).toBeNull()
})

it('keeps existing connections out of first-use onboarding', () => {
  localStorage.clear()
  sessionStorage.setItem('ov_studio_workspace_selected', 'selected')
  bootstrap.pathname = '/onboarding'
  render(<AppShell>{null}</AppShell>)
  expect(screen.getByText('/settings')).toBeTruthy()
  expect(screen.queryByRole('heading')).toBeNull()
})

describe('gateway navigation', () => {
  it.each([
    ['dev', true, true],
    ['api_key', true, true],
    ['trusted', true, true],
    ['api_key', false, false],
    ['offline', true, false],
    ['checking', true, false],
  ] as const)('%s allowed %s: %s', (serverMode, allowed, visible) => {
    expect(
      shouldShowGatewayNavigation({
        isGatewayHost: false,
        serverMode,
        allowed,
      }),
    ).toBe(visible)
  })

  it('always shows the entry on a standalone gateway', () => {
    expect(
      shouldShowGatewayNavigation({
        isGatewayHost: true,
        serverMode: 'offline',
        allowed: false,
      }),
    ).toBe(true)
  })
})

it('opens a fresh standalone Gateway Studio without the data-connection wizard', async () => {
  localStorage.clear()
  sessionStorage.clear()
  window.history.replaceState(null, '', '/studio/')
  bootstrap.health.mockResolvedValueOnce({
    service: 'openviking-gateway',
    auth_mode: 'api_key',
  })
  try {
    render(
      <AppShell>
        <span data-testid="business-route" />
      </AppShell>,
    )
    expect(await screen.findByTestId('connected-provider')).toBeTruthy()
    expect(bootstrap.health).toHaveBeenCalledWith(window.location.origin)
    expect(screen.queryByRole('heading')).toBeNull()
  } finally {
    window.history.replaceState(null, '', '/')
  }
})

it('renders first-use onboarding only at /onboarding', () => {
  localStorage.clear()
  sessionStorage.clear()
  bootstrap.pathname = '/onboarding'
  render(<AppShell>{null}</AppShell>)
  expect(screen.getByRole('heading', { level: 1 }).textContent).toContain(
    'Choose a service',
  )
  expect(screen.queryByTestId('connected-provider')).toBeNull()
})

it.each([true, false])(
  'redirects a mounted legacy /config surface with first-use=%s',
  (firstUse) => {
    localStorage.clear()
    sessionStorage.clear()
    if (!firstUse)
      sessionStorage.setItem('ov_studio_workspace_selected', 'selected')
    bootstrap.pathname = '/config'
    render(<AppShell>{null}</AppShell>)
    expect(
      screen.getByText(firstUse ? '/onboarding' : '/settings'),
    ).toBeTruthy()
    expect(screen.queryByRole('heading')).toBeNull()
  },
)
