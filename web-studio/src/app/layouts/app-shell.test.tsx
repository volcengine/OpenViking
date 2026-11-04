// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import {
  createMemoryHistory,
  createRouter,
  Outlet,
  RouterProvider,
} from '@tanstack/react-router'
import { routeTree } from '#/routeTree.gen'
import { AppShell } from './app-shell'
import { StudioRouteLayout } from '#/app/router/studio-route-layout'
import { AUTH_PATHS } from '#/features/auth/lib/auth-routes'
import i18n from '#/i18n'

const { health } = vi.hoisted(() => ({ health: vi.fn() }))
vi.mock('#/features/connection/lib/health', async (original) => ({
  ...(await original<object>()),
  fetchServerHealth: health,
}))
vi.mock('#/features/connection/lib/credential-storage', async (original) => ({
  ...(await original<object>()),
  readCredentialStorage: (key: string) => localStorage.getItem(key),
  writeCredentialStorage: (key: string, value: string) =>
    localStorage.setItem(key, value),
}))
vi.mock('#/features/connection/hooks/use-app-connection', async (original) => ({
  ...(await original<object>()),
  AppConnectionProvider: () => <div data-testid="connected-provider" />,
}))
vi.mock('#/app/router/root-layout', () => ({
  RootComponent: () => (
    <AppShell>
      <StudioRouteLayout>
        <Outlet />
      </StudioRouteLayout>
    </AppShell>
  ),
  RootErrorComponent: ({ error }: { error: Error }) => (
    <div>{error.message}</div>
  ),
}))
beforeEach(async () => {
  localStorage.clear()
  sessionStorage.clear()
  health.mockClear()
  await i18n.changeLanguage('en')
})
afterEach(() => {
  cleanup()
  delete window.__OV_STUDIO_CONFIG__
  window.history.replaceState(null, '', '/')
})

async function open(path: string, basepath = '') {
  window.history.replaceState(null, '', `${basepath}${path}`)
  const router = createRouter({
    routeTree,
    basepath,
    history: createMemoryHistory({ initialEntries: [`${basepath}${path}`] }),
  })
  const view = render(<RouterProvider router={router} />)
  await router.load()
  return { ...view, router }
}
function saved(
  baseUrl = 'https://private.example',
  serviceSelection = 'opensource',
) {
  localStorage.setItem(
    'ov_console_connection',
    JSON.stringify({
      baseUrl,
      serviceSelection,
      apiKey: 'synthetic',
      adminApiKey: '',
      accountId: 'team',
      userId: 'alice',
    }),
  )
}
it.each(AUTH_PATHS.flatMap((path) => [path, `${path}/`]))(
  'mounts public route %s without connected state or host discovery',
  async (path) => {
    saved()
    const { container } = await open(path, '/studio')
    await screen.findByRole('heading', { level: 1 })
    expect(screen.queryByTestId('connected-provider')).toBeNull()
    expect(health).not.toHaveBeenCalled()
    expect(container.querySelector('[data-studio-ux="workbench"]')).toBeTruthy()
  },
)
it.each(['/filesystem', '/onboarding', '/config'])(
  'redirects first use or legacy route %s to the real login route',
  async (path) => {
    const { router } = await open(path)
    await screen.findByRole('heading', {
      level: 1,
      name: 'Connect to OpenViking',
    })
    expect(router.state.location.pathname.replace(/\/+$/, '')).toBe('/login')
    expect(screen.queryByTestId('connected-provider')).toBeNull()
  },
)
it('rejects an incompatible active profile before mounting connected state', async () => {
  saved()
  window.__OV_STUDIO_CONFIG__ = { edition: 'volcengine' }
  await open('/filesystem')
  await screen.findByRole('heading', {
    level: 1,
    name: i18n.t('onboarding:accountAuth.loginTitle'),
  })
  expect(screen.queryByTestId('connected-provider')).toBeNull()
  expect(health).not.toHaveBeenCalled()
})
it('keeps official management deep links out of connected management', async () => {
  const endpoint = 'https://cloud.example/ov'
  window.__OV_STUDIO_CONFIG__ = {
    edition: 'volcengine',
    hostedApiBaseUrl: endpoint,
  }
  saved(endpoint, 'volcengine')
  const { router } = await open('/admin/settings')
  await waitFor(() =>
    expect(router.state.location.pathname).toBe('/filesystem'),
  )
  expect(screen.getByTestId('connected-provider')).toBeTruthy()
})
it.each([
  ['/filesystem', true],
  ['/settings/', true],
  ['/connections/new', true],
  ['/home', false],
] as const)(
  'applies scoped theme to %s independently of connection layout',
  async (path, themed) => {
    saved()
    const { container } = await open(path)
    await screen.findByTestId('connected-provider')
    expect(
      Boolean(container.querySelector('[data-studio-ux="workbench"]')),
    ).toBe(themed)
  },
)
it('uses route components when switching between default and explicit API Key login', async () => {
  window.__OV_STUDIO_CONFIG__ = { edition: 'volcengine' }
  const { router } = await open('/login')
  await screen.findByRole('heading', {
    level: 1,
    name: i18n.t('onboarding:accountAuth.loginTitle'),
  })
  await router.navigate({ to: '/login/api-key' })
  await screen.findByLabelText('API Key')
  expect(screen.queryByTestId('connected-provider')).toBeNull()
})
