// @vitest-environment jsdom
import { useState } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { IdentityScopedAppShell } from './app-shell'

const state = vi.hoisted(() => ({
  pathname: '/filesystem',
  mode: 'checking',
  profile: 'one',
}))
beforeEach(() =>
  Object.assign(state, {
    pathname: '/filesystem',
    mode: 'checking',
    profile: 'one',
  }),
)
vi.mock('#/hooks/use-mobile', () => ({ useIsMobile: () => false }))
vi.mock('#/components/account-switcher', () => ({
  AccountSwitcher: () => null,
}))
vi.mock('#/components/current-user-menu', () => ({
  CurrentUserMenu: () => null,
}))
vi.mock('#/components/generated-credential-dialog', () => ({
  GeneratedCredentialDialog: () => null,
}))
vi.mock('#/components/cross-device-verify-dialog', () => ({
  CrossDeviceVerifyDialog: () => null,
}))
vi.mock('#/hooks/use-studio-service', () => ({
  useStudioService: () => ({
    ready: state.mode !== 'checking',
    provider: 'opensource',
  }),
}))
vi.mock('#/hooks/use-studio-capabilities', () => ({
  useStudioCapabilities: () => ({}),
}))
vi.mock('#/routes/gateway/-lib/use-gateway', () => ({
  useGateway: () => ({ allowed: false }),
}))
vi.mock('#/hooks/use-app-connection', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  useAppConnection: () => ({
    identityScopeKey: `${state.profile}:${state.mode}`,
    activeProfileId: state.profile,
    serverMode: state.mode,
    connection: {
      baseUrl: 'http://service.test',
      adminApiKey: '',
      apiKey: '',
      accountId: 'default',
      userId: 'default',
    },
    connectionRole: 'unknown',
    isConnectionRoleLoading: false,
  }),
}))
vi.mock('@tanstack/react-router', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  Link: ({
    to,
    children,
    ...props
  }: {
    to: string
    children?: React.ReactNode
  }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
  useNavigate: () => vi.fn(),
  useRouterState: ({
    select,
  }: {
    select: (state: { location: { pathname: string } }) => unknown
  }) => select({ location: { pathname: state.pathname } }),
}))
afterEach(cleanup)
it('keeps settings reachable while a health request is pending', () => {
  state.pathname = '/filesystem'
  render(
    <IdentityScopedAppShell>
      <span data-testid="business-route" />
    </IdentityScopedAppShell>,
  )
  expect(screen.queryByTestId('business-route')).toBeNull()
  expect(
    screen
      .getByRole('button', { name: 'Connection settings' })
      .getAttribute('href'),
  ).toBe('/settings')
})
it('mounts the configuration screen without waiting for health', () => {
  state.pathname = '/settings'
  render(
    <IdentityScopedAppShell>
      <span data-testid="connection-settings" />
    </IdentityScopedAppShell>,
  )
  expect(screen.getByTestId('connection-settings')).toBeTruthy()
})

const RECOVERY_LABELS = {
  url: 'Recovery URL',
  key: 'Recovery Key',
  add: 'Add configuration',
  dialog: 'Configuration draft',
}

function RecoveryForm() {
  const [url, setUrl] = useState('http://saved.test')
  const [key, setKey] = useState('')
  const [open, setOpen] = useState(false)
  return (
    <>
      <input
        aria-label={RECOVERY_LABELS.url}
        value={url}
        onChange={(event) => setUrl(event.target.value)}
      />
      <input
        aria-label={RECOVERY_LABELS.key}
        value={key}
        onChange={(event) => setKey(event.target.value)}
      />
      <button onClick={() => setOpen(true)}>{RECOVERY_LABELS.add}</button>
      {open ? <div role="dialog">{RECOVERY_LABELS.dialog}</div> : null}
    </>
  )
}
it.each(['offline', 'api_key', 'trusted', 'dev'])(
  'retains recovery inputs and the configuration dialog after checking becomes %s',
  (mode) => {
    state.pathname = '/settings'
    const content = () => (
      <IdentityScopedAppShell>
        <RecoveryForm />
      </IdentityScopedAppShell>
    )
    const view = render(content())
    fireEvent.change(screen.getByLabelText('Recovery URL'), {
      target: { value: 'http://edited.test' },
    })
    fireEvent.change(screen.getByLabelText('Recovery Key'), {
      target: { value: 'synthetic-draft' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add configuration' }))
    state.mode = mode
    view.rerender(content())
    expect(screen.getByLabelText<HTMLInputElement>('Recovery URL').value).toBe(
      'http://edited.test',
    )
    expect(screen.getByLabelText<HTMLInputElement>('Recovery Key').value).toBe(
      'synthetic-draft',
    )
    expect(screen.getByRole('dialog')).toBeTruthy()
  },
)
it('resets settings drafts when a different profile is activated', () => {
  state.pathname = '/settings'
  const content = () => (
    <IdentityScopedAppShell>
      <RecoveryForm />
    </IdentityScopedAppShell>
  )
  const view = render(content())
  fireEvent.change(screen.getByLabelText('Recovery URL'), {
    target: { value: 'http://edited.test' },
  })
  state.profile = 'two'
  view.rerender(content())
  expect(screen.getByLabelText<HTMLInputElement>('Recovery URL').value).toBe(
    'http://saved.test',
  )
})
