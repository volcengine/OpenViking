import { getStudioExperience } from '#/platform/deployment/experience'
// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
  useRouterState,
} from '@tanstack/react-router'
import i18n from '#/i18n'
import {
  AUTH_PATHS,
  isAuthPath,
  resolveAuthPage,
} from '#/features/auth/lib/auth-routes'
import { AuthEntry } from '#/app/composition/auth-entry'

const complete = vi.fn()
beforeEach(async () => {
  localStorage.clear()
  sessionStorage.clear()
  complete.mockClear()
  await i18n.changeLanguage('en')
})
afterEach(() => {
  cleanup()
  delete window.__OV_STUDIO_CONFIG__
  vi.restoreAllMocks()
})

async function open(path: string, basepath = '') {
  const root = createRootRoute({
    component: () => {
      const pathname = useRouterState({
        select: (state) => state.location.pathname,
      }).replace(/\/+$/, '')
      return isAuthPath(pathname) ? (
        <AuthEntry
          page={resolveAuthPage(
            pathname,
            getStudioExperience().auth.defaultMethod,
          )}
          onComplete={complete}
        />
      ) : null
    },
  })
  const router = createRouter({
    routeTree: root.addChildren(
      AUTH_PATHS.map((entry) =>
        createRoute({
          getParentRoute: () => root,
          path: entry,
          component: () => null,
        }),
      ),
    ),
    basepath,
    history: createMemoryHistory({ initialEntries: [`${basepath}${path}`] }),
  })
  render(<RouterProvider router={router} />)
  await screen.findByRole('heading', { level: 1 })
  return router
}

it.each(['self-hosted', 'volcengine'] as const)(
  'chooses the %s default and allows switching login methods',
  async (edition) => {
    window.__OV_STUDIO_CONFIG__ = { edition }
    const router = await open('/login', '/studio')
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(
      edition === 'self-hosted'
        ? 'Connect to OpenViking'
        : 'Sign in to OpenViking',
    )
    fireEvent.click(
      screen.getByRole('link', {
        name:
          edition === 'self-hosted'
            ? 'Sign in with email'
            : 'Sign in with API Key',
      }),
    )
    await waitFor(() =>
      expect(router.state.location.pathname).toBe(
        edition === 'self-hosted' ? '/login/email' : '/login/api-key',
      ),
    )
    expect(router.history.location.pathname).toBe(
      `/studio${router.state.location.pathname}`,
    )
  },
)

it('carries email through recovery and back without putting it in a URL or storing a password', async () => {
  const router = await open('/login/email', '/studio')
  fireEvent.change(screen.getByLabelText('Work email'), {
    target: { value: 'alice@example.com' },
  })
  fireEvent.change(screen.getByLabelText('Password', { exact: true }), {
    target: { value: 'test-only-password' },
  })
  fireEvent.click(screen.getByRole('link', { name: 'Forgot password?' }))
  await screen.findByRole('heading', { name: 'Forgot password' })
  expect(screen.getByLabelText<HTMLInputElement>('Work email').value).toBe(
    'alice@example.com',
  )
  expect(router.history.location.href).not.toContain('alice')
  fireEvent.click(screen.getByRole('link', { name: 'Back to email sign-in' }))
  await screen.findByRole('heading', { name: 'Sign in to OpenViking' })
  expect(screen.getByLabelText<HTMLInputElement>('Work email').value).toBe(
    'alice@example.com',
  )
  expect(
    screen.getByLabelText<HTMLInputElement>('Password', { exact: true }).value,
  ).toBe('')
  expect(JSON.stringify(localStorage)).not.toContain('test-only-password')
  expect(JSON.stringify(router.history.location.state)).not.toContain(
    'test-only-password',
  )
})

it.each(['/login/email', '/forgot-password'])(
  'validates %s and never reports account success without the service',
  async (path) => {
    const router = await open(path)
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    const storage = JSON.stringify(localStorage)
    const submit = screen.getByRole('button', {
      name: path === '/forgot-password' ? 'Send reset link' : 'Sign in',
    })
    fireEvent.click(submit)
    expect(screen.getByText('Enter a valid email address.')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Work email'), {
      target: { value: 'alice@example.com' },
    })
    if (path === '/login/email') {
      fireEvent.change(screen.getByLabelText('Password', { exact: true }), {
        target: { value: 'synthetic-password' },
      })
      fireEvent.click(screen.getByRole('button', { name: 'Show Password' }))
      expect(
        screen.getByLabelText<HTMLInputElement>('Password', { exact: true })
          .type,
      ).toBe('text')
    }
    fireEvent.click(submit)
    expect(screen.getByRole('alert').textContent).toContain('not available')
    expect(router.state.location.pathname).toBe(path)
    expect(complete).not.toHaveBeenCalled()
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(JSON.stringify(localStorage)).toBe(storage)
    expect(sessionStorage.length).toBe(0)
  },
)

it('shows a missing reset link with recovery and validates the token-bearing reset form', async () => {
  const router = await open('/reset-password')
  expect(screen.getByText(/reset link is incomplete/)).toBeTruthy()
  expect(screen.queryByLabelText('New password')).toBeNull()
  router.history.push('/reset-password?token=synthetic-reset-token')
  await screen.findByLabelText('New password')
  fireEvent.change(screen.getByLabelText('New password'), {
    target: { value: 'short' },
  })
  fireEvent.change(screen.getByLabelText('Confirm password'), {
    target: { value: 'mismatch' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Reset password' }))
  expect(
    screen.getByLabelText('New password').getAttribute('aria-invalid'),
  ).toBe('true')
  expect(screen.getByText('The passwords do not match.')).toBeTruthy()
  fireEvent.change(screen.getByLabelText('New password'), {
    target: { value: 'Synthetic123!' },
  })
  fireEvent.change(screen.getByLabelText('Confirm password'), {
    target: { value: 'Synthetic123!' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Reset password' }))
  expect(screen.getByRole('alert').textContent).toContain('not available')
  expect(screen.getByLabelText<HTMLInputElement>('New password').value).toBe('')
  expect(complete).not.toHaveBeenCalled()
  expect(screen.queryByText('synthetic-reset-token')).toBeNull()
})

it.each(['/invite', '/invite#token=synthetic-invitation'])(
  'handles %s without inventing invitation details or membership',
  async (path) => {
    await open(path, '/studio')
    expect(
      screen.getByRole('heading', { name: 'Join a team workspace' }),
    ).toBeTruthy()
    if (path.includes('#'))
      expect(screen.getByRole('status').textContent).toContain('not available')
    else expect(screen.getByText(/invitation link is incomplete/)).toBeTruthy()
    expect(screen.queryByText('synthetic-invitation')).toBeNull()
    expect(sessionStorage.length).toBe(0)
    expect(complete).not.toHaveBeenCalled()
  },
)
