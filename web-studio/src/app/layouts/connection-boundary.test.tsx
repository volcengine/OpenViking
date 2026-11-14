// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState  } from 'react'
import type {ReactNode} from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { IdentityScopedAppShell } from './connection-boundary'

const state = vi.hoisted(() => ({
  pathname: '/admin/dashboard',
  account: 'first',
  role: 'root',
  mode: 'management',
  key: 'root-key',
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    identityScopeKey: `${state.account}:${state.key}`,
    activeProfileId: 'one',
    serverMode: 'api_key',
    studioMode: state.mode,
    connectionRole: state.role,
    connection: {
      baseUrl: 'http://server.test',
      accountId: state.account,
      userId: 'admin',
      apiKey: '',
      adminApiKey: state.key,
    },
  }),
}))
vi.mock('#/app/layouts/studio-layout', () => ({
  StudioLayout: ({ children }: { children: ReactNode }) => (
    <div data-testid="layout">{children}</div>
  ),
}))
vi.mock('#/app/layouts/studio-feature-boundary', () => ({
  StudioFeatureBoundary: ({ children }: { children: ReactNode }) => children,
}))
vi.mock('@tanstack/react-router', () => ({
  useRouterState: ({ select }: any) =>
    select({ location: { pathname: state.pathname } }),
}))
beforeEach(() =>
  Object.assign(state, {
    pathname: '/admin/dashboard',
    account: 'first',
    role: 'root',
    mode: 'management',
    key: 'root-key',
  }),
)
afterEach(cleanup)
function Draft() {
  const [value, setValue] = useState('')
  return (
    <input
      aria-label="draft"
      value={value}
      onChange={(event) => setValue(event.target.value)}
    />
  )
}
function mount() {
  const tree = () => (
    <IdentityScopedAppShell>
      <Draft />
    </IdentityScopedAppShell>
  )
  const view = render(tree())
  fireEvent.change(screen.getByLabelText('draft'), {
    target: { value: 'kept' },
  })
  return {
    layout: screen.getByTestId('layout'),
    refresh: () => view.rerender(tree()),
  }
}
it.each(['/admin/dashboard', '/admin/request-logs', '/admin/monitoring'])(
  'preserves the shell and filter state when Root selects another Account on %s',
  (pathname) => {
    state.pathname = pathname
    const view = mount()
    state.account = 'second'
    view.refresh()
    expect(screen.getByTestId('layout')).toBe(view.layout)
    expect((screen.getByLabelText('draft')).value).toBe(
      'kept',
    )
  },
)
it('keeps the shell but resets write forms across Account changes', () => {
  state.pathname = '/admin/users/permissions'
  const view = mount()
  state.account = 'second'
  view.refresh()
  expect(screen.getByTestId('layout')).toBe(view.layout)
  expect((screen.getByLabelText('draft')).value).toBe('')
})
it('resets the shell if the control credential changes', () => {
  const view = mount()
  state.key = 'different-root-key'
  view.refresh()
  expect(screen.getByTestId('layout')).not.toBe(view.layout)
})
it('retains identity isolation in the workbench', () => {
  state.pathname = '/filesystem'
  state.mode = 'workbench'
  state.role = 'user'
  const view = mount()
  state.account = 'second'
  view.refresh()
  expect(screen.getByTestId('layout')).not.toBe(view.layout)
  expect((screen.getByLabelText('draft')).value).toBe('')
})
