// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { AccountSwitcher } from './account-switcher'

const { activateProfile, activeState } = vi.hoisted(() => ({
  activateProfile: vi.fn(),
  activeState: {
    provider: 'volcengine' as 'volcengine' | 'opensource',
    profileId: 'study',
  },
}))

vi.mock('#/hooks/use-studio-service', () => ({
  useStudioService: () => ({ provider: activeState.provider }),
}))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection:
      activeState.provider === 'opensource'
        ? {
            accountId: 'work-memory',
            userId: 'wangladi',
            apiKey: 'opensource-key',
            adminApiKey: '',
            baseUrl: 'http://devbox.example:1933',
            serviceSelection: 'opensource',
          }
        : {
            accountId: 'study',
            userId: 'default',
            apiKey: 'study-key',
            adminApiKey: '',
            baseUrl: 'https://hosted.example/openviking',
            serviceSelection: 'volcengine',
          },
    profiles: [
      {
        id: 'study-copy',
        name: 'Study copy',
        connection: {
          accountId: 'study',
          userId: 'default',
          apiKey: 'older-study-key',
          baseUrl: 'https://hosted.example/openviking',
          serviceSelection: 'volcengine',
        },
      },
      {
        id: 'study-identical',
        name: 'Study identical',
        connection: {
          accountId: 'study',
          userId: 'default',
          apiKey: 'study-key',
          baseUrl: 'https://hosted.example/openviking',
          serviceSelection: 'volcengine',
        },
      },
      {
        id: 'study-same-name',
        name: 'Study',
        connection: {
          accountId: 'study',
          userId: 'default',
          apiKey: 'third-study-key',
          baseUrl: 'https://hosted.example/openviking',
          serviceSelection: 'volcengine',
        },
      },
      {
        id: 'study',
        name: 'Study',
        connection: {
          accountId: 'study',
          userId: 'default',
          apiKey: 'study-key',
          baseUrl: 'https://hosted.example/openviking',
          serviceSelection: 'volcengine',
        },
      },
      {
        id: 'study-alice',
        name: 'Study Alice',
        connection: {
          accountId: 'study',
          userId: 'alice',
          apiKey: 'alice-key',
          baseUrl: 'https://hosted.example/openviking',
          serviceSelection: 'volcengine',
        },
      },
      {
        id: 'default',
        name: 'Default',
        connection: {
          accountId: 'default',
          userId: 'default',
          apiKey: 'default-key',
          baseUrl: 'https://hosted.example/openviking',
          serviceSelection: 'volcengine',
        },
      },
      {
        id: 'unconfigured',
        name: 'Unconfigured',
        connection: {
          accountId: 'other',
          apiKey: '',
          baseUrl: 'https://hosted.example/openviking',
          serviceSelection: 'volcengine',
        },
      },
      {
        id: 'opensource-memory',
        name: 'Work memory',
        connection: {
          accountId: 'work-memory',
          userId: 'wangladi',
          apiKey: 'opensource-key',
          baseUrl: 'http://devbox.example:1933',
          serviceSelection: 'opensource',
        },
      },
      {
        id: 'opensource-duplicate',
        name: 'Duplicate work memory',
        connection: {
          accountId: 'work-memory',
          userId: 'wangladi',
          apiKey: 'opensource-key',
          baseUrl: 'http://devbox.example:1933',
          serviceSelection: 'opensource',
        },
      },
      {
        id: 'opensource-shared-url',
        name: 'Open-source shared URL',
        connection: {
          accountId: 'study',
          userId: 'default',
          apiKey: 'study-key',
          baseUrl: 'https://hosted.example/openviking',
          serviceSelection: 'opensource',
        },
      },
      {
        id: 'opensource-development',
        name: 'Development server',
        connection: {
          accountId: 'default',
          userId: 'default',
          apiKey: '',
          baseUrl: 'http://devbox.example:1934',
          serviceSelection: 'opensource',
        },
      },
    ],
    activeProfileId: activeState.profileId,
    activateProfile,
    connectionRole: 'user',
    isConnectionRoleLoading: false,
    isGatewayHost: false,
    serverMode: 'api_key',
  }),
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({
    children,
    to,
    ...props
  }: React.PropsWithChildren<{ to: string }>) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

afterEach(() => {
  cleanup()
  activateProfile.mockClear()
  activeState.provider = 'volcengine'
  activeState.profileId = 'study'
})

it('keeps distinct keys and users selectable while deduplicating identical hosted connections', async () => {
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <AccountSwitcher />
    </QueryClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'connections.switch' }))
  await waitFor(() =>
    expect(screen.getByRole('button', { name: /study\/alice/ })).toBeTruthy(),
  )
  expect(
    screen
      .getAllByRole('button')
      .filter(
        (button) =>
          button.getAttribute('data-slot') !== 'popover-trigger' &&
          button.querySelector('[role="img"][aria-label="provider.hosted"]'),
      ),
  ).toHaveLength(6)
  const otherKey = screen.getByRole('button', { name: /Study copy/ })
  expect(otherKey).toBeTruthy()
  expect(screen.queryByRole('button', { name: /Study identical/ })).toBeNull()
  const selected = screen
    .getAllByRole('button')
    .find((button) => button.hasAttribute('aria-current'))
  expect(selected?.getAttribute('aria-current')).toBe('true')
  expect(selected?.textContent).toContain('study/default')
  expect(selected?.textContent).toContain('Study')
  expect(screen.getByRole('button', { name: /Unconfigured/ })).toBeTruthy()
  fireEvent.click(otherKey)
  expect(activateProfile).toHaveBeenCalledWith('study-copy')

  fireEvent.click(screen.getByRole('button', { name: 'connections.switch' }))
  fireEvent.click(screen.getByRole('button', { name: /study\/alice/ }))
  expect(activateProfile).toHaveBeenCalledWith('study-alice')
  client.clear()
})

it('lists saved open-source connections beside hosted connections without merging providers', async () => {
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <AccountSwitcher />
    </QueryClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'connections.switch' }))

  const openSource = await screen.findByRole('button', {
    name: /Work memory/,
  })
  expect(openSource).toBeTruthy()
  expect(screen.queryByText('Duplicate work memory')).toBeNull()
  expect(
    screen
      .getByRole('button', { name: /Open-source shared URL/ })
      .querySelector('[role="img"][aria-label="provider.opensource"]'),
  ).toBeNull()
  expect(
    screen.getByRole('button', { name: /Development server/ }),
  ).toBeTruthy()
  expect(screen.getByRole('button', { name: /study\/alice/ })).toBeTruthy()

  fireEvent.click(openSource)
  expect(activateProfile).toHaveBeenCalledWith('opensource-memory')

  fireEvent.click(screen.getByRole('button', { name: 'connections.switch' }))
  fireEvent.click(screen.getByRole('button', { name: /Development server/ }))
  expect(activateProfile).toHaveBeenCalledWith('opensource-development')
  client.clear()
})

it('opens the same saved connection list from an open-source profile without admin rights', async () => {
  activeState.provider = 'opensource'
  activeState.profileId = 'opensource-memory'
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <AccountSwitcher />
    </QueryClientProvider>,
  )

  fireEvent.click(screen.getByRole('button', { name: 'connections.switch' }))
  const selected = await screen.findByRole('button', { name: /Work memory/ })
  expect(selected.getAttribute('aria-current')).toBe('true')
  expect(screen.queryByText('Duplicate work memory')).toBeNull()
  expect(screen.getByRole('button', { name: /study\/alice/ })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /study\/alice/ }))
  expect(activateProfile).toHaveBeenCalledWith('study-alice')
  client.clear()
})

it('distinguishes hosted connections with the same identity and profile name', async () => {
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <AccountSwitcher />
    </QueryClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'connections.switch' }))

  const sameNamePattern = (name: string) =>
    name.includes('study/default') &&
    name.includes('Study') &&
    !name.includes('Study copy') &&
    !name.includes('Study identical')
  const accessibleNames = new Set<string>()
  const sameNameOptions = screen.getAllByRole('button', {
    name: (name) => {
      if (!sameNamePattern(name)) return false
      accessibleNames.add(name)
      return true
    },
  })
  expect(sameNameOptions).toHaveLength(2)
  expect(accessibleNames.size).toBe(2)

  const otherKey = sameNameOptions.find(
    (button) => button.getAttribute('aria-current') !== 'true',
  )
  expect(otherKey).toBeTruthy()
  fireEvent.click(otherKey!)
  expect(activateProfile).toHaveBeenCalledWith('study-same-name')

  fireEvent.click(screen.getByRole('button', { name: 'connections.switch' }))
  const active = screen
    .getAllByRole('button', { name: sameNamePattern })
    .find((button) => button.getAttribute('aria-current') === 'true')
  expect(active).toBeTruthy()
  fireEvent.click(active!)
  expect(activateProfile).toHaveBeenCalledWith('study')
  client.clear()
})

it('shows a single default for personal hosted connections', async () => {
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <AccountSwitcher />
    </QueryClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'connections.switch' }))
  await waitFor(() =>
    expect(screen.getByRole('button', { name: /\[default\]/ })).toBeTruthy(),
  )
  const hostedDefault = screen.getByRole('button', { name: /\[default\]/ })
  expect(hostedDefault.textContent).not.toContain('default/default')
  fireEvent.click(hostedDefault)
  expect(activateProfile).toHaveBeenCalledWith('default')
  client.clear()
})
