// @vitest-environment jsdom
import { IdentityScopedAppShell } from '#/app/layouts/connection-boundary'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  pathname: '/filesystem',
  mode: 'checking',
  profile: 'one',
  surface: 'workbench',
  role: 'unknown',
  control: '',
  provider: 'opensource',
}))
beforeEach(() =>
  Object.assign(state, {
    pathname: '/filesystem',
    mode: 'checking',
    profile: 'one',
    surface: 'workbench',
    role: 'unknown',
    control: '',
    provider: 'opensource',
  }),
)
vi.mock('#/shared/hooks/use-mobile', () => ({ useIsMobile: () => false }))
vi.mock('#/features/connection/components/studio-scope-switcher', () => ({
  StudioScopeSwitcher: () => null,
}))
vi.mock('#/features/connection/components/generated-credential-dialog', () => ({
  GeneratedCredentialDialog: () => null,
}))
vi.mock('#/features/oauth/components/cross-device-verify-dialog', () => ({
  CrossDeviceVerifyDialog: () => null,
}))
vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({
    ready: state.mode !== 'checking',
    provider: state.provider,
  }),
}))
vi.mock('#/features/connection/hooks/use-studio-capabilities', () => ({
  useStudioCapabilities: () => ({}),
}))
vi.mock('#/features/admin/gateway/lib/use-gateway', () => ({
  useGateway: () => ({ allowed: false }),
}))
vi.mock('#/features/connection/hooks/use-app-connection', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  useAppConnection: () => ({
    identityScopeKey: `${state.profile}:${state.mode}`,
    activeProfileId: state.profile,
    profiles: [],
    activateProfile: vi.fn(),
    serverMode: state.mode,
    connection: {
      baseUrl: 'http://service.test',
      adminApiKey: state.control,
      apiKey: '',
      accountId: 'default',
      userId: 'default',
    },
    connectionRole: state.role,
    studioMode: state.surface,
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
it.each([
  ['/settings', '/settings'],
  ['/connections/new', '/settings'],
  ['/admin/settings', '/admin/settings'],
  ['/admin/connections/one', '/admin/settings'],
])(
  'mounts %s without waiting for health and keeps connections in the header menu',
  (path, destination) => {
    state.pathname = path
    render(
      <IdentityScopedAppShell>
        <span data-testid="connection-settings" />
      </IdentityScopedAppShell>,
    )
    expect(screen.getByTestId('connection-settings')).toBeTruthy()
    expect(
      screen.queryByRole('link', { name: /Connection settings/i }),
    ).toBeNull()
    if (path.startsWith('/admin'))
      expect(screen.getByText('Administration')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Studio settings/i }))
    const link = screen.getByRole('link', { name: /Manage connections/i })
    expect(link.getAttribute('href')).toBe(destination)
  },
)

it.each(['opensource', 'volcengine'])(
  'keeps the header connection menu reachable offline for %s',
  (provider) => {
    state.provider = provider
    state.mode = 'offline'
    render(
      <IdentityScopedAppShell>
        <span />
      </IdentityScopedAppShell>,
    )
    expect(
      screen.queryByRole('link', { name: /Connection settings/i }),
    ).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Studio settings/i }))
    expect(
      screen
        .getByRole('link', { name: /Manage connections/i })
        .getAttribute('href'),
    ).toBe('/settings')
    expect(screen.getByRole('group', { name: 'Language' })).toBeTruthy()
  },
)

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
it.each(
  ['/settings', '/admin/settings'].flatMap((path) =>
    ['offline', 'api_key', 'trusted', 'dev'].map((mode) => [path, mode]),
  ),
)('retains recovery inputs at %s after checking becomes %s', (path, mode) => {
  state.pathname = path
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
})
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

it.each([
  ['root', '/admin/settings'],
  ['user', '/admin/settings'],
])(
  'keeps gateway routes in management with authorized navigation for %s',
  (role, destination) => {
    state.mode = 'api_key'
    state.pathname = '/admin/gateway/profiles'
    state.surface = 'management'
    state.role = role
    state.control = 'stored-control-key'
    render(
      <IdentityScopedAppShell>
        <span data-testid="shared-page" />
      </IdentityScopedAppShell>,
    )
    fireEvent.click(screen.getByRole('button', { name: /Studio settings/i }))
    expect(
      screen
        .getByRole('link', { name: /Manage connections/i })
        .getAttribute('href'),
    ).toBe(destination)
    if (role === 'root') {
      expect(screen.queryByRole('link', { name: 'Home' })).toBeNull()
      expect(screen.getByRole('link', { name: 'Users' })).toBeTruthy()
    } else {
      expect(screen.queryByRole('link', { name: 'Home' })).toBeNull()
      expect(screen.queryByRole('link', { name: 'Users' })).toBeNull()
    }
  },
)

it('keeps gateway management out of the workbench even when a Root credential is saved', () => {
  state.mode = 'api_key'
  state.pathname = '/home'
  state.role = 'root'
  state.control = 'root-key'
  render(
    <IdentityScopedAppShell>
      <span />
    </IdentityScopedAppShell>,
  )
  expect(screen.queryByRole('link', { name: 'Gateway' })).toBeNull()
  expect(
    screen.queryByRole('link', { name: 'OpenViking configuration' }),
  ).toBeNull()
})

it('keeps management setup reachable from an ordinary User workbench without a control key', () => {
  state.pathname = '/settings'
  state.mode = 'api_key'
  state.surface = 'workbench'
  state.role = 'user'
  state.control = ''
  render(
    <IdentityScopedAppShell>
      <span data-testid="settings" />
    </IdentityScopedAppShell>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'Admin console' }))
  expect(
    screen.getByRole('dialog', { name: 'Configure management access' }),
  ).toBeTruthy()
  expect(screen.getByLabelText('Management API Key')).toBeTruthy()
  expect(screen.getByText('http://service.test')).toBeTruthy()
})
