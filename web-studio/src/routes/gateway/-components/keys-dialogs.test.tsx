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
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { GatewayError } from '../-lib/api'
import type * as Api from '../-lib/api'
import type { IssuedKey, KeyUser, Profile, Upstream } from '../-lib/api'
import type { Translate } from '../-lib/localize'
import { PROFILE_DEFAULTS } from '../-lib/profile-schema'
import { UPSTREAM_DEFAULTS } from '../-lib/upstream-schema'
import {
  KeysIssueDialog,
  issueProblem,
  validateKeyRequest,
} from './keys-issue-dialog'
import { KeysSecretDialog, snippetModel } from './keys-secret-dialog'

const api = vi.hoisted(() => ({ issueKey: vi.fn(), listKeyUsers: vi.fn() }))
const state = vi.hoisted(() => ({ role: 'admin' }))

vi.mock('../-lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof Api>()),
  issueKey: api.issueKey,
  listKeyUsers: api.listKeyUsers,
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
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
    connectionRole: state.role,
    isConnectionRoleLoading: false,
    serverMode: 'api_key',
  }),
}))
vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof TanStackRouter>()),
  Link: (props: { to: string; children?: React.ReactNode }) => (
    <a href={props.to}>{props.children}</a>
  ),
}))

afterEach(cleanup)
beforeEach(() => {
  vi.clearAllMocks()
  state.role = 'admin'
  api.listKeyUsers.mockResolvedValue([alice])
})

const t = ((key: string) => key) as unknown as Translate

function upstream(overrides: Partial<Upstream>): Upstream {
  return {
    ...UPSTREAM_DEFAULTS,
    id: 'u1',
    revision: 1,
    name: 'DeepSeek',
    protocol: 'chat',
    base_url: 'https://api.deepseek.com',
    has_api_key: true,
    header_names: [],
    ...overrides,
  }
}

const deepseek = upstream({
  models: ['deepseek-chat'],
  aliases: { fast: 'deepseek-chat' },
})
const openai = upstream({ id: 'u2', name: 'OpenAI', models: ['gpt-5'] })
const profile: Profile = {
  ...PROFILE_DEFAULTS,
  id: 'p1',
  revision: 1,
  name: 'Coding',
}

const alice: KeyUser = {
  user_id: 'alice',
  role: 'user',
  api_key_available: true,
}
const boss: KeyUser = {
  user_id: 'boss',
  role: 'admin',
  api_key_available: true,
}
const hashed: KeyUser = {
  user_id: 'hashed',
  role: 'user',
  api_key_available: false,
}

const settings = {
  name: 'Alice',
  policy_id: 'p1',
  upstream_ids: ['u1'],
  models: [],
}
const request = { ...settings, user_id: 'alice' }
const pasted = { ...settings, openviking_key: 'ov-key' }

describe('validateKeyRequest', () => {
  it('accepts a complete request for a user or with a pasted key', () => {
    expect(validateKeyRequest(request)).toEqual({})
    expect(validateKeyRequest(pasted)).toEqual({})
  })

  it('requires every field but the model list', () => {
    const empty = { name: ' ', policy_id: '', upstream_ids: [], models: [] }
    const missing = {
      name: 'validation.required',
      policy_id: 'validation.required',
      upstream_ids: 'keys.form.upstreams.required',
    }
    expect(validateKeyRequest({ ...empty, user_id: '' })).toEqual({
      ...missing,
      user_id: 'validation.required',
    })
    expect(validateKeyRequest({ ...empty, openviking_key: '' })).toEqual({
      ...missing,
      openviking_key: 'validation.required',
    })
  })

  it('rejects a gateway key pasted as the OpenViking key', () => {
    expect(
      validateKeyRequest({ ...settings, openviking_key: 'ovgw_abc' }),
    ).toEqual({ openviking_key: 'keys.form.openvikingKey.gatewayKey' })
  })
})

describe('issueProblem', () => {
  it.each([
    [
      'Unknown OpenViking user in this account',
      400,
      'keys.errors.unknownUser',
      true,
    ],
    [
      "This user's OpenViking key cannot be read on the server; paste the user's OpenViking key instead",
      409,
      'keys.errors.userKeyUnreadable',
      true,
    ],
    ['root_key_not_allowed', 403, 'keys.errors.rootKey', true],
    [
      'OpenViking key belongs to another account',
      403,
      'keys.errors.otherAccount',
      true,
    ],
    ['openviking_http_401', 401, 'keys.errors.invalidKey', true],
    [
      'openviking_identity_missing',
      401,
      'enums.openviking.identityMissing',
      true,
    ],
    ['openviking_unavailable', 503, 'keys.errors.unavailable', false],
    ['openviking_version_mismatch', 503, 'keys.errors.versionMismatch', false],
    ['Unknown upstream', 400, 'errors.unknownUpstream', false],
  ])('maps %s', (detail, status, message, keyField) => {
    expect(issueProblem(t, new GatewayError(detail, status))).toEqual({
      message,
      keyField,
    })
  })
})

describe('snippetModel', () => {
  it('prefers an allowed model the upstreams serve', () => {
    expect(snippetModel(['other', 'fast'], [deepseek])).toBe('fast')
    expect(snippetModel(['other'], [deepseek])).toBe('other')
    expect(snippetModel([], [deepseek])).toBe('deepseek-chat')
    expect(snippetModel([], [])).toBeUndefined()
  })
})

function renderIssueDialog(upstreams: Upstream[] = [deepseek]) {
  const onIssued = vi.fn()
  const onOpenChange = vi.fn()
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <KeysIssueDialog
        open
        onOpenChange={onOpenChange}
        profiles={[profile]}
        upstreams={upstreams}
        onIssued={onIssued}
      />
    </QueryClientProvider>,
  )
  return { onIssued, onOpenChange }
}

const userPicker = () =>
  screen.getByRole('combobox', { name: 'keys.form.user.label' })

function fillName() {
  fireEvent.change(screen.getByLabelText('keys.form.name.label'), {
    target: { value: 'Alice' },
  })
}

/** Names the key and waits for the only available user to be preselected. */
async function fillRequiredFields() {
  fillName()
  await waitFor(() => expect(userPicker().textContent).toContain('alice'))
}

function pasteKey(value: string) {
  fireEvent.click(
    screen.getByRole('button', { name: 'keys.form.openvikingKey.paste' }),
  )
  fireEvent.change(screen.getByLabelText('keys.form.openvikingKey.label'), {
    target: { value },
  })
}

const submit = () =>
  fireEvent.click(screen.getByRole('button', { name: 'keys.form.submit' }))

describe('KeysIssueDialog', () => {
  it('lists the account users and disables those whose key the server cannot read', async () => {
    api.listKeyUsers.mockResolvedValue([hashed, boss, alice])
    api.issueKey.mockResolvedValue({})
    renderIssueDialog()
    fillName()
    await waitFor(() =>
      expect(userPicker().textContent).toContain('keys.form.user.placeholder'),
    )
    // Several users can be picked, so none is chosen for the admin.
    submit()
    expect(await screen.findByText('validation.required')).toBeTruthy()
    expect(api.issueKey).not.toHaveBeenCalled()

    fireEvent.click(userPicker())
    const options = await screen.findAllByRole('option')
    expect(options.map((option) => option.textContent)).toEqual([
      'alicekeys.form.user.roles.user',
      'bosskeys.form.user.roles.admin',
      'hashedkeys.form.user.roles.userkeys.form.user.unavailable',
    ])
    expect(options[2].getAttribute('aria-disabled')).toBe('true')
    expect(options[1].getAttribute('aria-disabled')).not.toBe('true')
    // Base UI selects only the highlighted option; hovering highlights it.
    fireEvent.mouseMove(options[1])
    fireEvent.click(options[1])
    submit()

    await waitFor(() =>
      expect(api.issueKey).toHaveBeenCalledWith(expect.anything(), {
        ...request,
        user_id: 'boss',
      }),
    )
  })

  it('preselects the only user whose key the server can read', async () => {
    api.listKeyUsers.mockResolvedValue([alice, hashed])
    api.issueKey.mockResolvedValue({})
    renderIssueDialog()
    await fillRequiredFields()
    submit()

    await waitFor(() =>
      expect(api.issueKey).toHaveBeenCalledWith(expect.anything(), request),
    )
  })

  it('switches to pasting an OpenViking key and back', async () => {
    api.issueKey.mockResolvedValue({})
    renderIssueDialog()
    await fillRequiredFields()
    pasteKey('  ov-key  ')
    expect(
      screen.queryByRole('combobox', { name: 'keys.form.user.label' }),
    ).toBeNull()
    fireEvent.click(
      screen.getByRole('button', { name: 'keys.form.user.choose' }),
    )
    expect(userPicker().textContent).toContain('alice')
    // The pasted key survives switching back and forth.
    fireEvent.click(
      screen.getByRole('button', { name: 'keys.form.openvikingKey.paste' }),
    )
    submit()

    await waitFor(() =>
      expect(api.issueKey).toHaveBeenCalledWith(expect.anything(), pasted),
    )
  })

  it('moves focus to the field that a switch shows', async () => {
    renderIssueDialog()
    await fillRequiredFields()
    fireEvent.click(
      screen.getByRole('button', { name: 'keys.form.openvikingKey.paste' }),
    )
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByLabelText('keys.form.openvikingKey.label'),
      ),
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'keys.form.user.choose' }),
    )
    await waitFor(() => expect(document.activeElement).toBe(userPicker()))
  })

  it('asks root for a pasted key without listing users', async () => {
    // A root key may act on another account than the one Studio shows.
    state.role = 'root'
    api.issueKey.mockResolvedValue({})
    renderIssueDialog()
    fillName()

    expect(screen.getByText('keys.form.user.root')).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: 'keys.form.user.choose' }),
    ).toBeNull()
    fireEvent.change(screen.getByLabelText('keys.form.openvikingKey.label'), {
      target: { value: 'ov-key' },
    })
    submit()

    await waitFor(() =>
      expect(api.issueKey).toHaveBeenCalledWith(expect.anything(), pasted),
    )
    expect(api.listKeyUsers).not.toHaveBeenCalled()
  })

  it('keeps a removed user unselected instead of choosing another', async () => {
    api.listKeyUsers.mockResolvedValue([alice, boss])
    api.issueKey.mockRejectedValue(
      new GatewayError('Unknown OpenViking user in this account', 400),
    )
    renderIssueDialog()
    fillName()
    await waitFor(() =>
      expect(userPicker().textContent).toContain('keys.form.user.placeholder'),
    )
    fireEvent.click(userPicker())
    const options = await screen.findAllByRole('option')
    fireEvent.mouseMove(options[1])
    fireEvent.click(options[1])
    // boss is removed meanwhile; alice is now the only user left.
    api.listKeyUsers.mockResolvedValue([alice])
    submit()

    expect(await screen.findByText('keys.errors.unknownUser')).toBeTruthy()
    await waitFor(() => expect(api.listKeyUsers).toHaveBeenCalledTimes(2))
    await waitFor(() =>
      expect(userPicker().textContent).toContain('keys.form.user.placeholder'),
    )
    expect(screen.getByText('keys.errors.unknownUser')).toBeTruthy()
    submit()
    expect(api.issueKey).toHaveBeenCalledTimes(1)
  })

  it('retries a preselected user rather than another one', async () => {
    api.issueKey.mockRejectedValue(
      new GatewayError('Unknown OpenViking user in this account', 400),
    )
    renderIssueDialog()
    await fillRequiredFields()
    // alice is replaced by bob, who would otherwise be preselected.
    api.listKeyUsers.mockResolvedValue([{ ...alice, user_id: 'bob' }])
    submit()

    expect(await screen.findByText('keys.errors.unknownUser')).toBeTruthy()
    await waitFor(() => expect(api.listKeyUsers).toHaveBeenCalledTimes(2))
    await waitFor(() =>
      expect(userPicker().textContent).toContain('keys.form.user.placeholder'),
    )
  })

  it.each([
    ['the users cannot be listed', 'keys.form.user.loadFailed', false],
    ['no user key can be read', 'keys.form.user.none', true],
  ])('asks for a pasted key when %s', async (_case, reason, listed) => {
    if (listed) api.listKeyUsers.mockResolvedValue([hashed])
    else api.listKeyUsers.mockRejectedValue(new GatewayError('Forbidden', 403))
    renderIssueDialog()

    expect(await screen.findByText(reason)).toBeTruthy()
    expect(screen.getByLabelText('keys.form.openvikingKey.label')).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: 'keys.form.user.choose' }),
    ).toBeNull()
  })

  it('explains an unreadable user key next to the picker', async () => {
    api.issueKey.mockRejectedValue(
      new GatewayError(
        "This user's OpenViking key cannot be read on the server; paste the user's OpenViking key instead",
        409,
      ),
    )
    renderIssueDialog()
    await fillRequiredFields()
    submit()

    expect(
      await screen.findByText('keys.errors.userKeyUnreadable'),
    ).toBeTruthy()
    expect(userPicker().getAttribute('aria-invalid')).toBe('true')
    expect(screen.queryByText('keys.errors.title')).toBeNull()
  })

  it('shows what is missing instead of sending an incomplete request', async () => {
    renderIssueDialog([deepseek, openai])
    await fillRequiredFields()
    submit()

    expect(screen.getByText('keys.form.upstreams.required')).toBeTruthy()
    expect(api.issueKey).not.toHaveBeenCalled()
  })

  it('sends the chosen upstreams and allowed models', async () => {
    const issued: IssuedKey = {
      id: 'k1',
      revision: 1,
      name: 'Alice',
      policy_id: 'p1',
      upstream_ids: ['u2'],
      models: ['gpt-5'],
      user_id: 'alice',
      prefix: 'ovgw_Ab3dE9x',
      created_at: 1_700_000_000,
      key: 'ovgw_secret',
    }
    api.issueKey.mockResolvedValue(issued)
    const { onIssued } = renderIssueDialog([deepseek, openai])
    await fillRequiredFields()
    fireEvent.click(screen.getAllByRole('checkbox')[1])
    // Suggestions come from the selected upstreams only.
    expect(screen.queryByRole('button', { name: 'deepseek-chat' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'gpt-5' }))
    submit()

    await waitFor(() => expect(onIssued).toHaveBeenCalledWith(issued))
    expect(api.issueKey).toHaveBeenCalledWith(expect.anything(), {
      ...request,
      upstream_ids: ['u2'],
      models: ['gpt-5'],
    })
  })

  it('explains a rejected OpenViking key next to that field', async () => {
    api.issueKey.mockRejectedValue(
      new GatewayError('root_key_not_allowed', 403),
    )
    const { onIssued } = renderIssueDialog()
    fillName()
    pasteKey('ov-key')
    submit()

    expect(await screen.findByText('keys.errors.rootKey')).toBeTruthy()
    expect(
      screen
        .getByLabelText('keys.form.openvikingKey.label')
        .getAttribute('aria-invalid'),
    ).toBe('true')
    expect(screen.queryByText('keys.errors.title')).toBeNull()
    expect(onIssued).not.toHaveBeenCalled()

    // Editing the key clears the server's verdict.
    fireEvent.change(screen.getByLabelText('keys.form.openvikingKey.label'), {
      target: { value: 'another-key' },
    })
    expect(screen.queryByText('keys.errors.rootKey')).toBeNull()
  })

  it('shows other failures in an alert inside the dialog', async () => {
    api.issueKey.mockRejectedValue(
      new GatewayError('openviking_unavailable', 503),
    )
    renderIssueDialog()
    await fillRequiredFields()
    submit()

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('keys.errors.title')
    expect(alert.textContent).toContain('keys.errors.unavailable')
  })
})

describe('KeysSecretDialog', () => {
  const issued: IssuedKey = {
    id: 'k1',
    revision: 1,
    name: 'Alice',
    policy_id: 'p1',
    upstream_ids: ['u1'],
    models: [],
    user_id: 'alice',
    prefix: 'ovgw_Ab3dE9x',
    created_at: 1_700_000_000,
    key: 'ovgw_theSecret',
  }

  function renderSecret(upstreams = [deepseek]) {
    const onDone = vi.fn()
    render(
      <KeysSecretDialog
        open
        issued={issued}
        baseUrl="https://gw.example.com"
        profileName="Coding"
        upstreams={upstreams}
        onDone={onDone}
      />,
    )
    return onDone
  }

  it('cannot be dismissed with Escape and has no close button', () => {
    const onDone = renderSecret()
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    fireEvent.keyDown(document.body, { key: 'Escape' })

    expect(screen.getByText('keys.secret.title')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'ui.close' })).toBeNull()
    expect(onDone).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'keys.secret.done' }))
    expect(onDone).toHaveBeenCalledTimes(1)
  })

  it('opens on a client the key can serve, with the real key and address', () => {
    renderSecret()
    expect(screen.getByText('alice')).toBeTruthy()
    expect(screen.getByText('Coding')).toBeTruthy()
    // Only a Chat Completions upstream: the chat client tab is selected.
    const snippet = screen.getByText(/chat\.completions\.create/)
    expect(snippet.textContent).toContain('https://gw.example.com/v1')
    expect(snippet.textContent).toContain('ovgw_theSecret')
    expect(snippet.textContent).toContain('deepseek-chat')
    expect(screen.queryByText('keys.secret.noProtocol')).toBeNull()
  })

  it('warns when the key has no upstream for a client', async () => {
    renderSecret()
    fireEvent.click(
      screen.getByRole('tab', {
        name: 'connect.clients.claude-code.name',
      }),
    )
    expect(await screen.findByText('keys.secret.noProtocol')).toBeTruthy()
  })

  it('treats a disabled upstream as missing, like the Connect page', () => {
    renderSecret([{ ...deepseek, enabled: false }])
    expect(screen.getByText('keys.secret.noProtocol')).toBeTruthy()
    expect(screen.queryByText(/deepseek-chat/)).toBeNull()
  })
})
