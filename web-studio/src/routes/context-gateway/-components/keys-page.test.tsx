// @vitest-environment jsdom
import type * as React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type * as TanStackRouter from '@tanstack/react-router'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as Api from '../-lib/api'
import type { GatewayKey, IssuedKey, Profile, Upstream } from '../-lib/api'
import { PROFILE_DEFAULTS } from '../-lib/profile-schema'
import { UPSTREAM_DEFAULTS } from '../-lib/upstream-schema'
import { KeysPage } from './keys-page'

const api = vi.hoisted(() => ({
  getConnectionInfo: vi.fn(),
  listKeys: vi.fn(),
  listProfiles: vi.fn(),
  listUpstreams: vi.fn(),
  listKeyUsers: vi.fn(),
  issueKey: vi.fn(),
  revokeKey: vi.fn(),
  deleteUserData: vi.fn(),
}))
const toastMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))

vi.mock('../-lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof Api>()),
  ...api,
}))
vi.mock('sonner', () => ({ toast: toastMocks }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: 'en' },
  }),
}))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'http://localhost:1933',
      accountId: 'acme',
      userId: 'admin',
      apiKey: '',
      adminApiKey: 'admin-key',
    },
    connectionRole: 'admin',
    isConnectionRoleLoading: false,
    serverMode: 'api_key',
  }),
}))
vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof TanStackRouter>()),
  Link: (props: {
    to: string
    params?: Record<string, string>
    className?: string
    title?: string
    children?: React.ReactNode
  }) => (
    <a
      href={Object.entries(props.params ?? {}).reduce(
        (path, [name, value]) => path.replace(`$${name}`, value),
        props.to,
      )}
      className={props.className}
      title={props.title}
    >
      {props.children}
    </a>
  ),
}))

const BASE_URL = 'https://gateway.example.com'

const profile: Profile = {
  ...PROFILE_DEFAULTS,
  id: 'p1',
  revision: 1,
  name: 'Coding',
}

function upstream(overrides: Partial<Upstream>): Upstream {
  return {
    ...UPSTREAM_DEFAULTS,
    id: 'u1',
    revision: 1,
    name: 'Claude',
    protocol: 'anthropic',
    base_url: 'https://api.anthropic.com',
    has_api_key: true,
    header_names: [],
    ...overrides,
  }
}

const claude = upstream({ models: ['claude-sonnet-4-5'] })
const deepseek = upstream({
  id: 'u2',
  name: 'DeepSeek',
  protocol: 'chat',
  models: ['deepseek-chat'],
})

const laptop: GatewayKey = {
  id: 'k1',
  revision: 1,
  name: 'Alice laptop',
  policy_id: 'p1',
  upstream_ids: ['u1', 'u2'],
  models: [],
  user_id: 'alice',
  prefix: 'ovcg_Ab3dE9x',
  created_at: 1_700_000_000,
}
const ci: GatewayKey = {
  ...laptop,
  id: 'k2',
  name: 'CI bot',
  policy_id: 'gone',
  upstream_ids: ['u2'],
  models: ['deepseek-chat'],
  user_id: 'bob',
  prefix: 'ovcg_Zz9yX8w',
  created_at: 1_600_000_000,
}

const connection = expect.objectContaining({ apiKey: 'admin-key' })

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <KeysPage />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getConnectionInfo.mockResolvedValue({
    base_url: BASE_URL,
    public_url_configured: true,
  })
  api.listProfiles.mockResolvedValue([profile])
  api.listUpstreams.mockResolvedValue([claude, deepseek])
  api.listKeys.mockResolvedValue([ci, laptop])
  api.listKeyUsers.mockResolvedValue([
    { user_id: 'carol', role: 'user', api_key_available: true },
  ])
})
afterEach(cleanup)

describe('KeysPage', () => {
  it('lists keys newest first with their user, profile, upstreams and models', async () => {
    renderPage()

    const rows = await screen.findAllByRole('row')
    // Header row, then the newest key.
    expect(within(rows[1]).getByText('Alice laptop')).toBeTruthy()
    expect(within(rows[1]).getByText('ovcg_Ab3dE9x…')).toBeTruthy()
    expect(within(rows[1]).getByText('alice')).toBeTruthy()
    expect(
      within(rows[1])
        .getByRole('link', { name: 'Coding' })
        .getAttribute('href'),
    ).toBe('/context-gateway/profiles/p1')
    expect(within(rows[1]).getByText('Claude')).toBeTruthy()
    expect(within(rows[1]).getByText('DeepSeek')).toBeTruthy()
    expect(within(rows[1]).getByText('states.any')).toBeTruthy()

    expect(within(rows[2]).getByText('CI bot')).toBeTruthy()
    expect(within(rows[2]).getByText('keys.missing')).toBeTruthy()
    expect(within(rows[2]).getByText('deepseek-chat')).toBeTruthy()
  })

  it('revokes a key after confirmation', async () => {
    api.revokeKey.mockResolvedValue({ deleted: true })
    renderPage()

    const [revokeLaptop] = await screen.findAllByRole('button', {
      name: 'keys.actions.revokeKey',
    })
    fireEvent.click(revokeLaptop)
    expect(screen.getByText('keys.revoke.title')).toBeTruthy()
    expect(api.revokeKey).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'keys.revoke.confirm' }))
    await waitFor(() =>
      expect(api.revokeKey).toHaveBeenCalledWith(connection, 'k1'),
    )
    await waitFor(() =>
      expect(toastMocks.success).toHaveBeenCalledWith('keys.revoke.done'),
    )
    await waitFor(() => expect(api.listKeys).toHaveBeenCalledTimes(2))
  })

  it('keeps the confirmation open and reports a failed revoke', async () => {
    api.revokeKey.mockRejectedValue(new Error('boom'))
    renderPage()

    const [revokeLaptop] = await screen.findAllByRole('button', {
      name: 'keys.actions.revokeKey',
    })
    fireEvent.click(revokeLaptop)
    fireEvent.click(screen.getByRole('button', { name: 'keys.revoke.confirm' }))

    await waitFor(() => expect(toastMocks.error).toHaveBeenCalledWith('boom'))
    expect(screen.getByText('keys.revoke.title')).toBeTruthy()
  })

  it('explains what is missing before the first key can be issued', async () => {
    api.listKeys.mockResolvedValue([])
    api.listUpstreams.mockResolvedValue([])
    renderPage()

    expect(await screen.findByText('keys.empty.title')).toBeTruthy()
    expect(await screen.findByText('keys.prerequisites.upstream')).toBeTruthy()
    expect(
      screen
        .getByRole('link', { name: /keys.prerequisites.addUpstream/ })
        .getAttribute('href'),
    ).toBe('/context-gateway/upstreams/new')
    expect(
      screen.queryByRole('link', { name: /keys.prerequisites.createProfile/ }),
    ).toBeNull()
    const issue = screen.getByRole('button', { name: 'keys.issue' })
    expect((issue as HTMLButtonElement).disabled).toBe(true)
  })

  it('issues a key and shows the secret once with ready-to-use snippets', async () => {
    api.listUpstreams.mockResolvedValue([claude])
    const issued: IssuedKey = {
      ...laptop,
      id: 'k3',
      name: 'Carol',
      upstream_ids: ['u1'],
      key: 'ovcg_secretSecretSecret',
    }
    api.issueKey.mockResolvedValue(issued)
    renderPage()

    const issue = await screen.findByRole('button', { name: 'keys.issue' })
    await waitFor(() =>
      expect((issue as HTMLButtonElement).disabled).toBe(false),
    )
    fireEvent.click(issue)

    fireEvent.change(screen.getByLabelText('keys.form.name.label'), {
      target: { value: '  Carol  ' },
    })
    // The account's only user is picked for the admin.
    const user = screen.getByRole('combobox', { name: 'keys.form.user.label' })
    await waitFor(() => expect(user.textContent).toContain('carol'))
    fireEvent.click(screen.getByRole('button', { name: 'keys.form.submit' }))

    await waitFor(() =>
      expect(api.issueKey).toHaveBeenCalledWith(connection, {
        name: 'Carol',
        user_id: 'carol',
        policy_id: 'p1',
        upstream_ids: ['u1'],
        models: [],
      }),
    )
    expect(await screen.findByText('keys.secret.title')).toBeTruthy()
    expect(screen.getByText('ovcg_secretSecretSecret')).toBeTruthy()
    expect(screen.queryByText('keys.form.title')).toBeNull()
    // Claude Code is the first client this key's upstreams can serve.
    const snippet = screen.getByText(/ANTHROPIC_BASE_URL/)
    expect(snippet.textContent).toContain(BASE_URL)
    expect(snippet.textContent).toContain('ovcg_secretSecretSecret')

    fireEvent.click(screen.getByRole('button', { name: 'keys.secret.done' }))
    await waitFor(() =>
      expect(screen.queryByText('keys.secret.title')).toBeNull(),
    )
  })
})
