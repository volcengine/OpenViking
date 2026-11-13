// @vitest-environment jsdom
import { StudioSettingsMenu } from '#/features/connection/components/studio-settings-menu'
import { readPreferredMode } from '#/features/connection/lib/studio-mode'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  connection: {
    baseUrl: 'http://service.test',
    accountId: 'team',
    userId: 'alice',
    apiKey: 'user-key',
    adminApiKey: 'root-key',
  },
  activateProfile: vi.fn(),
  changeLanguage: vi.fn(),
  setTheme: vi.fn(),
  role: 'root',
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: state.connection,
    profiles: [
      { id: 'active', name: 'Alice', connection: state.connection },
      {
        id: 'other',
        name: 'Bob',
        connection: { ...state.connection, userId: 'bob' },
      },
    ],
    activeProfileId: 'active',
    activateProfile: state.activateProfile,
    serverMode: 'api_key',
    connectionRole: state.role,
  }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'en', changeLanguage: state.changeLanguage },
  }),
}))
vi.mock('next-themes', () => ({
  useTheme: () => ({ setTheme: state.setTheme, resolvedTheme: 'light' }),
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    children,
    ...props
  }: {
    to: string
    children: React.ReactNode
    onClick?: React.MouseEventHandler<HTMLAnchorElement>
    className?: string
  }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}))
beforeEach(() => {
  localStorage.clear()
  state.connection.apiKey = 'user-key'
  state.connection.userId = 'alice'
  state.role = 'root'
  vi.clearAllMocks()
})
afterEach(cleanup)
function openMenu(canManage = true, managementMode = false) {
  render(
    <StudioSettingsMenu
      canManage={canManage}
      managementMode={managementMode}
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: 'studioSettings.open' }))
}
it('shows saved local connections and preferences without exposing credentials', () => {
  openMenu()
  const profiles = within(
    screen.getByRole('group', { name: 'studioSettings.connections' }),
  )
  expect(
    profiles
      .getByRole('button', { name: /Alice/ })
      .getAttribute('aria-pressed'),
  ).toBe('true')
  expect(profiles.getByRole('button', { name: /Bob/ })).toBeTruthy()
  expect(
    screen
      .getByRole('button', { name: 'studioSettings.open' })
      .getAttribute('title'),
  ).toBe('alice')
  expect(screen.queryByText('API Key')).toBeNull()
  expect(screen.queryByRole('switch')).toBeNull()
  expect(document.body.textContent).not.toContain('user-key')
  expect(document.body.textContent).not.toContain('root-key')
  expect(state.activateProfile).not.toHaveBeenCalled()
})
it('activates a saved connection and closes the preferences menu', () => {
  openMenu()
  fireEvent.click(screen.getByRole('button', { name: /Bob/ }))
  expect(state.activateProfile).toHaveBeenCalledWith('other')
  expect(
    screen.queryByRole('group', { name: 'studioSettings.connections' }),
  ).toBeNull()
})
it.each([
  [false, '/connections/new', '/settings'],
  [true, '/admin/connections/new', '/admin/settings'],
] as const)(
  'keeps connection routes within the current management=%s surface',
  (managementMode, addPath, managePath) => {
    openMenu(true, managementMode)
    expect(
      screen
        .getByRole('link', { name: 'studioSettings.addConnection' })
        .getAttribute('href'),
    ).toBe(addPath)
    expect(
      screen
        .getByRole('link', { name: 'studioSettings.manageConnections' })
        .getAttribute('href'),
    ).toBe(managePath)
  },
)
it.each([
  ['root', true, 'studioSettings.root'],
  ['admin', true, 'studioSettings.accountAdmin'],
  ['root', false, 'alice'],
  ['user', false, 'alice'],
] as const)(
  'shows the active namespace for %s with management=%s',
  (role, managementMode, label) => {
    state.role = role
    render(
      <StudioSettingsMenu
        canManage={role !== 'user'}
        managementMode={managementMode}
      />,
    )
    const trigger = screen.getByRole('button', { name: 'studioSettings.open' })
    expect(trigger.getAttribute('title')).toBe(label)
    expect(Boolean(screen.queryByText('AL'))).toBe(!managementMode)
  },
)
it('updates the user avatar and name when the workbench identity changes', () => {
  const view = render(<StudioSettingsMenu canManage managementMode={false} />)
  expect(screen.getByText('AL')).toBeTruthy()
  state.connection.userId = 'bob'
  view.rerender(<StudioSettingsMenu canManage managementMode={false} />)
  expect(screen.getByText('BO')).toBeTruthy()
  expect(
    screen
      .getByRole('button', { name: 'studioSettings.open' })
      .getAttribute('title'),
  ).toBe('bob')
  expect(screen.queryByText('alice')).toBeNull()
})
it('shows Root rather than a stale User ID when a Root connection has no data identity', () => {
  state.connection.apiKey = ''
  render(<StudioSettingsMenu canManage managementMode={false} />)
  expect(
    screen
      .getByRole('button', { name: 'studioSettings.open' })
      .getAttribute('title'),
  ).toBe('studioSettings.root')
  expect(screen.queryByText('alice')).toBeNull()
})
it('persists the default entry and keeps language and appearance controls together', () => {
  openMenu()
  const entries = within(
    screen.getByRole('group', { name: 'studioSettings.defaultEntry' }),
  )
  expect(
    entries
      .getByRole('button', { name: 'studioSettings.workbench' })
      .getAttribute('aria-pressed'),
  ).toBe('true')
  fireEvent.click(
    entries.getByRole('button', { name: 'studioSettings.workbench' }),
  )
  expect(readPreferredMode(state.connection, 'active')).toBe('workbench')
  fireEvent.click(screen.getByRole('button', { name: '中文' }))
  expect(state.changeLanguage).toHaveBeenCalledWith('zh-CN')
  fireEvent.click(screen.getByRole('button', { name: 'studioSettings.dark' }))
  expect(state.setTheme).toHaveBeenCalledWith('dark')
})
it('requires a data identity before choosing workbench as the default', () => {
  state.connection.apiKey = ''
  openMenu()
  expect(screen.queryByText('Root')).toBeNull()
  expect(
    screen
      .getByRole('button', { name: 'studioSettings.workbench' })
      .hasAttribute('disabled'),
  ).toBe(true)
})
it('does not offer administration preferences to a user connection', () => {
  openMenu(false)
  expect(
    screen.queryByRole('group', { name: 'studioSettings.defaultEntry' }),
  ).toBeNull()
  expect(
    screen
      .getByRole('link', { name: 'studioSettings.manageConnections' })
      .getAttribute('href'),
  ).toBe('/settings')
})
