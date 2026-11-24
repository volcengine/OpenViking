// @vitest-environment jsdom
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
import type * as React from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  KeysIssueDialog,
  issueProblem,
  validateKeyRequest,
} from '#/features/admin/gateway/components/keys-issue-dialog'
import {
  KeysSecretDialog,
  snippetModel,
} from '#/features/admin/gateway/components/keys-secret-dialog'
import type * as Api from '#/features/admin/gateway/lib/api'
import type {
  IssuedKey,
  KeyUser,
  Profile,
  Upstream,
} from '#/features/admin/gateway/lib/api'
import { GatewayError } from '#/features/admin/gateway/lib/api'
import { gatewayDocsUrl } from '#/features/admin/gateway/lib/client-guides'
import type { Translate } from '#/features/admin/gateway/lib/localize'
import { PROFILE_DEFAULTS } from '#/features/admin/gateway/lib/profile-schema'
import { UPSTREAM_DEFAULTS } from '#/features/admin/gateway/lib/upstream-schema'

const api = vi.hoisted(() => ({
  issueKey: vi.fn(),
  listKeyUsers: vi.fn(),
  getAccess: vi.fn(),
}))
const state = vi.hoisted(() => ({ role: 'admin', devMode: false }))

vi.mock('#/features/admin/gateway/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof Api>()),
  issueKey: api.issueKey,
  listKeyUsers: api.listKeyUsers,
  getAccess: api.getAccess,
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: 'en' },
  }),
}))
vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({ ready: true, provider: 'opensource' }),
}))

vi.mock('#/features/connection/hooks/use-app-connection', () => ({
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
    serverMode: state.devMode ? 'dev' : 'api_key',
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
  state.devMode = false
  api.getAccess.mockImplementation(async () => ({
    managed: false,
    can_write_objects: true,
    can_issue_for_others: state.role !== 'user' && !state.devMode,
    role: state.role,
    account_id: 'acme',
    user_id: 'admin',
    warnings: [],
  }))
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
const pasted = { ...settings, user_id: 'alice', openviking_key: 'ov-key' }

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

  it('accepts self issuance with no owner', () => {
    expect(validateKeyRequest(settings)).toEqual({})
  })

  it('rejects a management key pasted as the user API key', () => {
    expect(
      validateKeyRequest({ ...settings, openviking_key: 'ovgm_abc' }),
    ).toEqual({ openviking_key: 'keys.form.openvikingKey.managementKey' })
  })

  it('rejects a gateway key pasted as the OpenViking key', () => {
    expect(
      validateKeyRequest({ ...settings, openviking_key: 'ovgw_abc' }),
    ).toEqual({ openviking_key: 'keys.form.openvikingKey.gatewayKey' })
  })
})

describe('issueProblem', () => {
  it.each([
    ['unknown_user', 400, 'keys.errors.unknownUser', true],
    ['user_required', 400, 'keys.errors.userRequired', true],
    ['user_key_unreadable', 409, 'keys.errors.userKeyUnreadable', true],
    ['root_key_not_allowed', 403, 'keys.errors.rootKey', true],
    ['other_account', 403, 'keys.errors.otherAccount', true],
    ['openviking_http_401', 401, 'keys.errors.invalidKey', true],
    [
      'openviking_identity_missing',
      401,
      'enums.openviking.identityMissing',
      true,
    ],
    ['openviking_unavailable', 503, 'keys.errors.unavailable', false],
    ['openviking_version_mismatch', 503, 'keys.errors.versionMismatch', false],
  ])(
    'maps stable reason %s independent of detail',
    (reason, status, message, keyField) => {
      expect(
        issueProblem(
          t,
          new GatewayError(
            'arbitrary server wording',
            status,
            undefined,
            reason,
          ),
        ),
      ).toEqual({ message, keyField })
    },
  )
  it('includes the required version', () => {
    const translate = ((key: string, values?: { version?: string }) =>
      values?.version ?? key) as Translate
    expect(
      issueProblem(
        translate,
        new GatewayError(
          'upgrade',
          503,
          undefined,
          'openviking_version_mismatch',
          { required_version: '0.5.0' },
        ),
      ).message,
    ).toContain('0.5.0')
  })
  it('does not infer a reason from English detail', () => {
    expect(
      issueProblem(t, new GatewayError('root_key_not_allowed', 400)).keyField,
    ).toBe(false)
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

function renderIssueDialog(
  upstreams: Upstream[] = [deepseek],
  defaults?: React.ComponentProps<typeof KeysIssueDialog>['defaults'],
) {
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
        defaults={defaults}
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
  fireEvent.change(
    screen.getByLabelText(/keys.form.openvikingKey.(forUser|label)/),
    {
      target: { value },
    },
  )
}

const submit = () =>
  fireEvent.click(screen.getByRole('button', { name: 'keys.form.submit' }))

describe('KeysIssueDialog', () => {
  it('lists every account user including users whose key must be pasted', async () => {
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
    expect(options[2].getAttribute('aria-disabled')).not.toBe('true')
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

  it('defaults to the signed-in user when other users exist', async () => {
    api.listKeyUsers.mockResolvedValue([
      { ...boss, user_id: 'admin' },
      alice,
      hashed,
    ])
    api.issueKey.mockResolvedValue({})
    renderIssueDialog()
    fillName()
    await waitFor(() => expect(userPicker().textContent).toContain('admin'))
    submit()
    await waitFor(() =>
      expect(api.issueKey).toHaveBeenCalledWith(expect.anything(), {
        ...request,
        user_id: 'admin',
      }),
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
        screen.getByLabelText(/keys.form.openvikingKey.(forUser|label)/),
      ),
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'keys.form.user.choose' }),
    )
    await waitFor(() => expect(document.activeElement).toBe(userPicker()))
  })

  it('lets root choose a user in the selected account', async () => {
    state.role = 'root'
    api.issueKey.mockResolvedValue({})
    renderIssueDialog()
    await fillRequiredFields()
    submit()
    await waitFor(() =>
      expect(api.issueKey).toHaveBeenCalledWith(expect.anything(), request),
    )
    expect(api.listKeyUsers).toHaveBeenCalledWith(
      expect.objectContaining({ accountId: 'acme' }),
    )
  })

  it.each([false, true])(
    'issues for the current identity without an owner (dev: %s)',
    async (devMode) => {
      state.role = devMode ? 'root' : 'user'
      state.devMode = devMode
      api.issueKey.mockResolvedValue({})
      renderIssueDialog()
      fillName()
      await screen.findByText(
        devMode ? 'keys.form.user.developmentIdentity' : 'keys.form.user.self',
      )
      expect(
        screen.queryByRole('combobox', { name: 'keys.form.user.label' }),
      ).toBeNull()
      submit()
      await waitFor(() =>
        expect(api.issueKey).toHaveBeenCalledWith(expect.anything(), settings),
      )
      expect(api.listKeyUsers).not.toHaveBeenCalled()
    },
  )

  it('focuses the paste field when a selected user key is unreadable', async () => {
    api.listKeyUsers.mockResolvedValue([hashed])
    renderIssueDialog()
    const field = await screen.findByLabelText(
      'keys.form.openvikingKey.forUser',
    )
    await waitFor(() => expect(document.activeElement).toBe(field))
  })

  it('asks for a pasted key while an unreadable user is selected', async () => {
    api.listKeyUsers.mockResolvedValue([alice, hashed])
    api.issueKey.mockResolvedValue({})
    renderIssueDialog()
    fillName()
    await waitFor(() =>
      expect(userPicker().hasAttribute('disabled')).toBe(false),
    )
    const pick = async (name: RegExp) => {
      fireEvent.click(userPicker())
      const option = await screen.findByRole('option', { name })
      fireEvent.mouseMove(option)
      fireEvent.click(option)
    }
    await pick(/hashed/)
    const field = await screen.findByLabelText(
      'keys.form.openvikingKey.forUser',
    )
    await waitFor(() => expect(document.activeElement).toBe(field))
    // Choosing a user whose key the server can read drops the paste field.
    await pick(/alice/)
    await waitFor(() =>
      expect(
        screen.queryByLabelText('keys.form.openvikingKey.forUser'),
      ).toBeNull(),
    )
    submit()
    await waitFor(() =>
      expect(api.issueKey).toHaveBeenCalledWith(expect.anything(), request),
    )
  })

  it('never asks an admin to paste their own key', async () => {
    api.listKeyUsers.mockResolvedValue([
      { ...hashed, user_id: 'admin', role: 'admin' },
      alice,
    ])
    api.issueKey.mockResolvedValue({})
    renderIssueDialog()
    fillName()
    await waitFor(() => expect(userPicker().textContent).toContain('admin'))
    expect(
      screen.queryByLabelText('keys.form.openvikingKey.forUser'),
    ).toBeNull()
    submit()
    await waitFor(() =>
      expect(api.issueKey).toHaveBeenCalledWith(expect.anything(), {
        ...request,
        user_id: 'admin',
      }),
    )
  })

  it('switches and focuses the paste field on user_key_unreadable, then retries', async () => {
    api.issueKey
      .mockRejectedValueOnce(
        new GatewayError(
          'changed wording',
          409,
          undefined,
          'user_key_unreadable',
        ),
      )
      .mockResolvedValueOnce({})
    renderIssueDialog()
    await fillRequiredFields()
    submit()
    const field = await screen.findByLabelText(
      'keys.form.openvikingKey.forUser',
    )
    await waitFor(() => expect(document.activeElement).toBe(field))
    expect(screen.getByText('keys.errors.userKeyUnreadable')).toBeTruthy()
    fireEvent.change(field, { target: { value: 'ov-key' } })
    submit()
    await waitFor(() =>
      expect(api.issueKey).toHaveBeenLastCalledWith(expect.anything(), pasted),
    )
  })

  it('keeps a removed user unselected instead of choosing another', async () => {
    api.listKeyUsers.mockResolvedValue([alice, boss])
    api.issueKey.mockRejectedValue(
      new GatewayError(
        'Unknown OpenViking user in this account',
        400,
        undefined,
        'unknown_user',
      ),
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
      new GatewayError(
        'Unknown OpenViking user in this account',
        400,
        undefined,
        'unknown_user',
      ),
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
    if (listed) api.listKeyUsers.mockResolvedValue([])
    else api.listKeyUsers.mockRejectedValue(new GatewayError('Forbidden', 403))
    renderIssueDialog()

    expect(await screen.findByText(reason)).toBeTruthy()
    expect(
      screen.getByLabelText(/keys.form.openvikingKey.(forUser|label)/),
    ).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: 'keys.form.user.choose' }),
    ).toBeNull()
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

  it('starts from the given defaults', async () => {
    api.issueKey.mockResolvedValue({})
    renderIssueDialog([deepseek, openai], {
      name: 'Quick mode',
      policy_id: 'p1',
      upstream_ids: ['u2'],
    })
    await waitFor(() => expect(userPicker().textContent).toContain('alice'))
    submit()

    await waitFor(() =>
      expect(api.issueKey).toHaveBeenCalledWith(expect.anything(), {
        ...request,
        name: 'Quick mode',
        upstream_ids: ['u2'],
      }),
    )
  })

  it('explains a rejected OpenViking key next to that field', async () => {
    api.issueKey.mockRejectedValue(
      new GatewayError(
        'root_key_not_allowed',
        403,
        undefined,
        'root_key_not_allowed',
      ),
    )
    const { onIssued } = renderIssueDialog()
    await fillRequiredFields()
    pasteKey('ov-key')
    submit()

    expect(await screen.findByText('keys.errors.rootKey')).toBeTruthy()
    expect(
      screen
        .getByLabelText('keys.form.openvikingKey.forUser')
        .getAttribute('aria-invalid'),
    ).toBe('true')
    expect(screen.queryByText('keys.errors.title')).toBeNull()
    expect(onIssued).not.toHaveBeenCalled()

    // Editing the key clears the server's verdict.
    fireEvent.change(
      screen.getByLabelText(/keys.form.openvikingKey.(forUser|label)/),
      {
        target: { value: 'another-key' },
      },
    )
    expect(screen.queryByText('keys.errors.rootKey')).toBeNull()
  })

  it('rejects a management key pasted into the user-key field before sending', async () => {
    renderIssueDialog()
    await fillRequiredFields()
    pasteKey('ovgm_wrong_place')
    submit()
    expect(
      screen.getByText('keys.form.openvikingKey.managementKey'),
    ).toBeTruthy()
    expect(api.issueKey).not.toHaveBeenCalled()
  })

  it('labels blocked Coding Plan upstreams and keeps them selectable', async () => {
    api.issueKey.mockResolvedValue({})
    renderIssueDialog([
      { ...deepseek, coding_plan: true, allow_coding_plan: false },
    ])
    await fillRequiredFields()
    expect(screen.getByText('keys.form.upstreams.blocked')).toBeTruthy()
    const checkbox = screen.getByRole('checkbox')
    expect(checkbox.getAttribute('aria-disabled')).not.toBe('true')
    submit()
    await waitFor(() =>
      expect(api.issueKey).toHaveBeenCalledWith(expect.anything(), request),
    )
  })

  it('shows other failures in an alert inside the dialog', async () => {
    api.issueKey.mockRejectedValue(
      new GatewayError(
        'openviking_unavailable',
        503,
        undefined,
        'openviking_unavailable',
      ),
    )
    renderIssueDialog()
    await fillRequiredFields()
    submit()

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('keys.errors.title')
    expect(alert.textContent).toContain('keys.errors.unavailable')
    expect(
      within(alert)
        .getByRole('link', { name: 'docs.troubleshoot' })
        .getAttribute('href'),
    ).toBe(gatewayDocsUrl('issueKeyFails', 'en'))
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
        baseUrl="https://gw.example.com/gateway"
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
    expect(screen.getByText('keys.secret.identity')).toBeTruthy()
    expect(screen.getByText('keys.secret.dependency')).toBeTruthy()
    expect(screen.getByText('Coding')).toBeTruthy()
    // Only a Chat Completions upstream: the chat client tab is selected.
    const snippet = screen.getByText(/chat\.completions\.create/)
    expect(snippet.textContent).toContain('https://gw.example.com/gateway/v1')
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
