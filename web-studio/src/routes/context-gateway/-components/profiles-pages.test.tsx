// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  useParams,
  useSearch,
} from '@tanstack/react-router'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import type * as ReactI18next from 'react-i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as Api from '../-lib/api'
import type {
  GatewayKey,
  GatewayTool,
  Profile,
  ProfileSettings,
} from '../-lib/api'
import { PROFILE_DEFAULTS, WRITE_TOOLS } from '../-lib/profile-schema'
import { parseProfileEditorSearch } from '../-lib/search'
import { ProfilesPage } from './profiles-page'
import { ProfileEditor } from './profiles-editor'

const api = vi.hoisted(() => ({
  listProfiles: vi.fn(),
  listKeys: vi.fn(),
  listTools: vi.fn(),
  saveProfile: vi.fn(),
  deleteProfile: vi.fn(),
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
/** Keys, plus interpolation values as JSON so assertions can check them. */
const translate = vi.hoisted(
  () => (key: string, values?: Record<string, unknown>) =>
    values && Object.keys(values).length
      ? `${key} ${JSON.stringify(values)}`
      : key,
)

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
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof ReactI18next>()),
  useTranslation: () => ({
    t: translate,
    i18n: { resolvedLanguage: 'en' },
  }),
}))
vi.mock('sonner', () => ({ toast }))
vi.mock('../-lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof Api>()),
  ...api,
}))

const coding = {
  ...PROFILE_DEFAULTS,
  id: 'p1',
  revision: 3,
  name: 'Coding',
  capture: false,
  query_max_chars: 1234,
  quotas: { events: 2, skills: 1 },
  context_window: 64000,
  compaction_threshold: 0.8,
  agent_windows: true,
  idle_seconds: 30.5,
  gateway_tools: true,
  disabled_tools: ['read', 'removed_tool'],
  // A field this version of the form does not know about.
  future_setting: 'discard me',
} as Profile

const tools: GatewayTool[] = [
  { name: 'find', description: 'Semantic retrieval' },
  { name: 'search', description: 'Search conversations' },
  {
    name: 'read',
    description: 'Read a file',
    annotations: { readOnlyHint: true },
  },
  {
    name: 'write',
    description: 'Write a file',
    annotations: { readOnlyHint: false },
  },
  {
    name: 'future_tool',
    description: 'A newly added tool',
    annotations: { destructiveHint: true },
  },
]

const chat: Profile = {
  ...PROFILE_DEFAULTS,
  id: 'p2',
  revision: 1,
  name: 'Chat',
  recall: false,
  // Saved with OpenViking tools off; the recommended write exclusions remain.
  gateway_tools: false,
}

const key = (id: string, policyId: string): GatewayKey => ({
  id,
  revision: 1,
  name: id,
  policy_id: policyId,
  upstream_ids: ['u1'],
  models: [],
  user_id: 'alice',
  prefix: 'ovcg_abc',
  created_at: 1_700_000_000,
})

/** `profile` as the save body, without server metadata or unrecognized fields. */
function settingsOf(profile: Profile): ProfileSettings {
  const {
    id: _id,
    revision: _revision,
    future_setting: _future,
    ...settings
  } = profile as Profile & { future_setting?: string }
  return settings
}

function EditorRoute() {
  const { profileId } = useParams({ strict: false })
  const { from } = useSearch({ strict: false })
  return <ProfileEditor profileId={profileId ?? ''} from={from} />
}

function renderAt(path: string) {
  const root = createRootRoute({ component: Outlet })
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({
        getParentRoute: () => root,
        path: '/context-gateway/profiles',
        component: ProfilesPage,
      }),
      createRoute({
        getParentRoute: () => root,
        path: '/context-gateway/profiles/$profileId',
        validateSearch: parseProfileEditorSearch,
        component: EditorRoute,
      }),
    ]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

/** Save on an existing profile, "Create profile" on a new one. */
const saveButton = () =>
  screen.getByRole<HTMLButtonElement>('button', {
    name: /^(actions\.save|profiles\.editor\.create)$/,
  })
const nameInput = () =>
  screen.getByLabelText<HTMLInputElement>('profiles.name.label')
const sectionSwitch = (section: string) =>
  screen.getByRole('switch', { name: `profiles.${section}.title` })
/** A tool checkbox's accessible name starts with the tool name; its description follows. */
const toolName = (name: string) => new RegExp(`^${name}\\b`)

beforeEach(() => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  api.listProfiles.mockResolvedValue([coding, chat])
  api.listKeys.mockResolvedValue([key('k1', 'p1'), key('k2', 'p1')])
  api.listTools.mockResolvedValue(tools)
  api.saveProfile.mockImplementation(
    (_connection, id: string, settings: ProfileSettings) =>
      Promise.resolve({ ...settings, id, revision: 1 }),
  )
  api.deleteProfile.mockResolvedValue({ deleted: true })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('profile list', () => {
  const card = (name: string) =>
    screen.getByRole('link', { name }).closest<HTMLElement>('[data-slot=card]')!

  it('summarizes each profile and its keys', async () => {
    renderAt('/context-gateway/profiles')
    await screen.findByRole('link', { name: 'Coding' })

    const codingCard = within(card('Coding'))
    expect(
      codingCard.getByText('profiles.summary.recallOn {"tokens":"1,600"}'),
    ).toBeTruthy()
    // Long conversations do not depend on saving.
    expect(codingCard.getAllByText('states.off')).toHaveLength(1)
    expect(
      codingCard.getByText(
        'profiles.summary.compactionOn {"percent":"80%"} · profiles.summary.agentWindowsOn',
      ),
    ).toBeTruthy()
    expect(
      await codingCard.findByText('profiles.summary.toolsEnabled {"count":4}'),
    ).toBeTruthy()
    expect(codingCard.getByText('profiles.usedBy {"count":2}')).toBeTruthy()

    const chatCard = within(card('Chat'))
    expect(
      chatCard.getByText('profiles.summary.compactionOn {"percent":"90%"}'),
    ).toBeTruthy()
    expect(chatCard.getByText('profiles.unused')).toBeTruthy()
  })

  it('keeps tool counts unknown until the catalog loads', async () => {
    let resolveTools!: (value: GatewayTool[]) => void
    api.listTools.mockReturnValue(
      new Promise<GatewayTool[]>((resolve) => {
        resolveTools = resolve
      }),
    )
    renderAt('/context-gateway/profiles')
    await screen.findByRole('link', { name: 'Coding' })
    const codingCard = within(card('Coding'))
    expect(codingCard.getByText('states.on')).toBeTruthy()
    expect(codingCard.queryByText(/profiles.summary.toolsEnabled/)).toBeNull()
    resolveTools(tools)
    expect(
      await codingCard.findByText('profiles.summary.toolsEnabled {"count":4}'),
    ).toBeTruthy()
  })

  it('keeps profiles visible when tools fail to load and retries the catalog', async () => {
    api.listTools.mockRejectedValueOnce(new Error('Tools unavailable'))
    renderAt('/context-gateway/profiles')
    await screen.findByText('profiles.tools.loadFailed')
    expect(
      within(card('Coding')).queryByText(/profiles.summary.toolsEnabled/),
    ).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'actions.retry' }))
    expect(
      await within(card('Coding')).findByText(
        'profiles.summary.toolsEnabled {"count":4}',
      ),
    ).toBeTruthy()
  })

  it('blocks deleting a profile that keys use and deletes an unused one', async () => {
    renderAt('/context-gateway/profiles')
    await screen.findByText('profiles.usedBy {"count":2}')

    const blocked = within(card('Coding')).getByRole<HTMLButtonElement>(
      'button',
      { name: 'actions.delete' },
    )
    expect(blocked.disabled).toBe(true)
    expect(screen.getByTitle('profiles.deleteBlocked {"count":2}')).toBeTruthy()

    fireEvent.click(
      within(card('Chat')).getByRole('button', { name: 'actions.delete' }),
    )
    fireEvent.click(await screen.findByText('profiles.deleteDialog.confirm'))
    await waitFor(() =>
      expect(api.deleteProfile).toHaveBeenCalledWith(expect.anything(), 'p2'),
    )
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(
        'profiles.toast.deleted {"name":"Chat"}',
      ),
    )
  })

  it('creates a profile with the recommended settings in one click', async () => {
    api.listProfiles.mockResolvedValue([])
    renderAt('/context-gateway/profiles')
    fireEvent.click(
      await screen.findByText('profiles.actions.createRecommended'),
    )
    await waitFor(() => expect(api.saveProfile).toHaveBeenCalledTimes(1))
    const [, id, settings] = api.saveProfile.mock.calls[0]
    expect(id).toMatch(/^[0-9a-f]{16}$/)
    // Recommended settings under a name in the UI language.
    expect(settings).toEqual({
      ...PROFILE_DEFAULTS,
      name: 'profiles.defaultName',
    })
    expect(screen.getByText('profiles.actions.customize')).toBeTruthy()
  })

  it('duplicates into a prefilled editor that saves a new profile', async () => {
    const router = renderAt('/context-gateway/profiles')
    await screen.findByRole('link', { name: 'Coding' })
    fireEvent.click(
      within(card('Coding')).getByRole('button', { name: 'actions.duplicate' }),
    )

    await waitFor(() =>
      expect(router.state.location.pathname).toBe(
        '/context-gateway/profiles/new',
      ),
    )
    expect(router.state.location.search).toEqual({ from: 'p1' })
    const copyName = 'profiles.editor.copyName {"name":"Coding"}'
    expect((await screen.findByDisplayValue(copyName)).id).toBe('profile-name')

    fireEvent.click(saveButton())
    await waitFor(() => expect(api.saveProfile).toHaveBeenCalledTimes(1))
    const [, id, settings] = api.saveProfile.mock.calls[0]
    expect(id).not.toBe('p1')
    expect(settings).toEqual({ ...settingsOf(coding), name: copyName })
  })
})

describe('profile editor', () => {
  it('sends known stored fields back unchanged when only the name changes', async () => {
    const router = renderAt('/context-gateway/profiles/p1')
    expect((await screen.findByDisplayValue('Coding')).id).toBe('profile-name')
    expect(saveButton().disabled).toBe(true)

    fireEvent.change(nameInput(), { target: { value: 'Coding v2' } })
    expect(screen.getByText('profiles.editor.unsaved')).toBeTruthy()
    fireEvent.click(saveButton())

    await waitFor(() =>
      expect(api.saveProfile).toHaveBeenCalledWith(expect.anything(), 'p1', {
        ...settingsOf(coding),
        name: 'Coding v2',
      }),
    )
    await waitFor(() =>
      expect(router.state.location.pathname).toBe('/context-gateway/profiles'),
    )
  })

  it('hides settings of switched-off sections and switches long-conversation features on their own', async () => {
    renderAt('/context-gateway/profiles/p2')
    await screen.findByDisplayValue('Chat')

    expect(
      screen.queryByLabelText('profiles.recall.maxTokens.label'),
    ).toBeNull()
    fireEvent.click(sectionSwitch('recall'))
    expect(
      screen.getByLabelText('profiles.recall.maxTokens.label'),
    ).toBeTruthy()

    const threshold = 'profiles.longConversations.threshold.label'
    expect(screen.getByLabelText(threshold)).toBeTruthy()
    fireEvent.click(sectionSwitch('capture'))
    expect(screen.getByLabelText(threshold)).toBeTruthy()
    fireEvent.click(
      screen.getByRole('switch', {
        name: 'profiles.longConversations.compaction.label',
      }),
    )
    expect(screen.queryByLabelText(threshold)).toBeNull()

    const softRatio = 'profiles.longConversations.softRatio.label'
    const needsTools = 'profiles.longConversations.agentWindows.needsTools'
    expect(screen.queryByLabelText(softRatio)).toBeNull()
    expect(
      screen.getByText('profiles.longConversations.agentWindows.experimental'),
    ).toBeTruthy()
    fireEvent.click(
      screen.getByRole('switch', {
        name: 'profiles.longConversations.agentWindows.label',
      }),
    )
    expect(screen.getByLabelText(softRatio)).toBeTruthy()
    expect(screen.getByText(needsTools)).toBeTruthy()
    fireEvent.click(sectionSwitch('tools'))
    expect(screen.queryByText(needsTools)).toBeNull()

    fireEvent.click(saveButton())
    await waitFor(() => expect(api.saveProfile).toHaveBeenCalledTimes(1))
    expect(api.saveProfile.mock.calls[0][2]).toMatchObject({
      capture: false,
      compaction: false,
      agent_windows: true,
      gateway_tools: true,
    })
  })

  it('keeps reminder ratios in view while they conflict', async () => {
    renderAt('/context-gateway/profiles/p1')
    await screen.findByDisplayValue('Coding')
    fireEvent.change(
      screen.getByLabelText('profiles.longConversations.softRatio.label'),
      { target: { value: '0.9' } },
    )
    expect(screen.getByText('validation.softBelowHard')).toBeTruthy()
    expect(saveButton().disabled).toBe(true)

    fireEvent.click(
      screen.getByRole('switch', {
        name: 'profiles.longConversations.agentWindows.label',
      }),
    )
    expect(
      screen.getByLabelText('profiles.longConversations.softRatio.label'),
    ).toBeTruthy()
    expect(screen.getByText('field.sectionInvalid')).toBeTruthy()
  })

  it('shows limit errors inline and blocks saving', async () => {
    renderAt('/context-gateway/profiles/p1')
    await screen.findByDisplayValue('Coding')
    fireEvent.change(screen.getByLabelText('profiles.recall.maxTokens.label'), {
      target: { value: '10' },
    })
    expect(
      screen.getByText('validation.range {"min":64,"max":32000}'),
    ).toBeTruthy()
    expect(screen.getByText('profiles.editor.invalid')).toBeTruthy()
    expect(saveButton().disabled).toBe(true)
  })

  it('configures opening context independently while recall is off', async () => {
    renderAt('/context-gateway/profiles/p2')
    await screen.findByDisplayValue('Chat')
    fireEvent.click(
      screen.getByRole('switch', { name: 'profiles.recall.profile.label' }),
    )
    fireEvent.change(
      screen.getByLabelText('profiles.recall.profileMaxTokens.label'),
      {
        target: { value: '0' },
      },
    )
    fireEvent.click(saveButton())
    await waitFor(() => expect(api.saveProfile).toHaveBeenCalledTimes(1))
    expect(api.saveProfile.mock.calls[0][2]).toMatchObject({
      recall: false,
      profile: false,
      profile_max_tokens: 0,
    })
  })

  it('keeps invalid settings in view when their section is off or advanced', async () => {
    renderAt('/context-gateway/profiles/p1')
    await screen.findByDisplayValue('Coding')
    expect(screen.queryByText('field.sectionInvalid')).toBeNull()
    fireEvent.change(screen.getByLabelText('profiles.recall.maxTokens.label'), {
      target: { value: '10' },
    })
    fireEvent.click(sectionSwitch('recall'))
    expect(
      screen.getByLabelText('profiles.recall.maxTokens.label'),
    ).toBeTruthy()
    expect(screen.getByText('field.sectionInvalid')).toBeTruthy()
  })

  it('opens advanced settings that hold an error', async () => {
    api.listProfiles.mockResolvedValue([
      { ...chat, recall: true, quotas: { legacy: 2 } } as Profile,
    ])
    renderAt('/context-gateway/profiles/p2')
    await screen.findByDisplayValue('Chat')
    expect(
      screen.getByText('validation.unknownCategory {"name":"legacy"}'),
    ).toBeTruthy()
    expect(screen.getByText('field.sectionInvalid')).toBeTruthy()
    expect(saveButton().disabled).toBe(true)
  })

  it('leaves write tools unchecked by default and saves only unchecked raw names', async () => {
    renderAt('/context-gateway/profiles/p2')
    await screen.findByDisplayValue('Chat')
    expect(sectionSwitch('tools').getAttribute('aria-checked')).toBe('false')
    expect(api.listTools).not.toHaveBeenCalled()
    expect(
      screen.queryByRole('checkbox', { name: toolName('find') }),
    ).toBeNull()
    fireEvent.click(sectionSwitch('tools'))
    await screen.findByRole('checkbox', { name: toolName('find') })
    for (const tool of tools) {
      expect(
        screen
          .getByRole('checkbox', { name: toolName(tool.name) })
          .getAttribute('aria-checked'),
      ).toBe(String(!WRITE_TOOLS.includes(tool.name)))
      expect(screen.getByText(tool.description)).toBeTruthy()
    }
    expect(
      screen.queryByRole('switch', { name: 'profiles.tools.allowWrite.label' }),
    ).toBeNull()
    expect(screen.getByText('profiles.tools.executionNotice')).toBeTruthy()
    fireEvent.click(screen.getByRole('checkbox', { name: toolName('write') }))
    fireEvent.click(screen.getByRole('checkbox', { name: toolName('find') }))
    fireEvent.click(screen.getByRole('checkbox', { name: toolName('read') }))
    fireEvent.click(screen.getByRole('checkbox', { name: toolName('read') }))
    fireEvent.click(saveButton())

    await waitFor(() => expect(api.saveProfile).toHaveBeenCalledTimes(1))
    // Listed tools in catalog order, then exclusions the catalog lacks.
    expect(api.saveProfile.mock.calls[0][2]).toMatchObject({
      gateway_tools: true,
      disabled_tools: [
        'find',
        ...WRITE_TOOLS.filter((name) => name !== 'write'),
      ],
    })
    expect(api.saveProfile.mock.calls[0][2]).not.toHaveProperty(
      'allow_write_tools',
    )
    expect(api.saveProfile.mock.calls[0][2]).not.toHaveProperty(
      'tool_allowlist',
    )
  })

  it('uses only explicit readOnlyHint annotations for badges', async () => {
    renderAt('/context-gateway/profiles/p1')
    await screen.findByRole('checkbox', { name: toolName('read') })
    const labelFor = (name: string) =>
      within(
        screen
          .getByRole('checkbox', { name: toolName(name) })
          .closest('label')!,
      )
    expect(labelFor('read').getByText('profiles.tools.readOnly')).toBeTruthy()
    expect(
      labelFor('write').getByText('profiles.tools.modifiesData'),
    ).toBeTruthy()
    for (const name of ['find', 'search', 'future_tool']) {
      expect(labelFor(name).queryByText('profiles.tools.readOnly')).toBeNull()
      expect(
        labelFor(name).queryByText('profiles.tools.modifiesData'),
      ).toBeNull()
    }
  })

  it('preserves missing tool exclusions while re-enabling a listed tool', async () => {
    renderAt('/context-gateway/profiles/p1')
    const readTool = await screen.findByRole('checkbox', {
      name: toolName('read'),
    })
    expect(readTool.getAttribute('aria-checked')).toBe('false')
    expect(
      screen
        .getByRole('checkbox', { name: toolName('future_tool') })
        .getAttribute('aria-checked'),
    ).toBe('true')
    fireEvent.click(readTool)
    fireEvent.click(saveButton())
    await waitFor(() => expect(api.saveProfile).toHaveBeenCalledTimes(1))
    expect(api.saveProfile.mock.calls[0][2].disabled_tools).toEqual([
      'removed_tool',
    ])
  })

  it('allows disabling every tool', async () => {
    renderAt('/context-gateway/profiles/p2')
    await screen.findByDisplayValue('Chat')
    fireEvent.click(sectionSwitch('tools'))
    await screen.findByRole('checkbox', { name: toolName('find') })
    for (const tool of tools) {
      const box = screen.getByRole('checkbox', { name: toolName(tool.name) })
      if (box.getAttribute('aria-checked') === 'true') fireEvent.click(box)
    }
    expect(saveButton().disabled).toBe(false)
    fireEvent.click(saveButton())
    await waitFor(() => expect(api.saveProfile).toHaveBeenCalledTimes(1))
    expect(api.saveProfile.mock.calls[0][2].disabled_tools).toEqual([
      ...tools.map((tool) => tool.name),
      ...WRITE_TOOLS.filter((name) => name !== 'write'),
    ])
  })

  it('explains the empty catalog and still lets a profile be saved', async () => {
    api.listTools.mockResolvedValue([])
    renderAt('/context-gateway/profiles/p2')
    await screen.findByDisplayValue('Chat')
    fireEvent.click(sectionSwitch('tools'))
    expect(await screen.findByText('profiles.tools.empty')).toBeTruthy()
    expect(saveButton().disabled).toBe(false)
    fireEvent.click(saveButton())
    await waitFor(() => expect(api.saveProfile).toHaveBeenCalledTimes(1))
    expect(api.saveProfile.mock.calls[0][2].disabled_tools).toEqual(WRITE_TOOLS)
  })

  it('shows a retry action when the tools request fails', async () => {
    api.listTools.mockRejectedValueOnce(new Error('Tools unavailable'))
    renderAt('/context-gateway/profiles/p1')
    expect(await screen.findByText('profiles.tools.loadFailed')).toBeTruthy()
    expect(screen.queryByText('profiles.tools.empty')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'actions.retry' }))
    expect(
      await screen.findByRole('checkbox', { name: toolName('future_tool') }),
    ).toBeTruthy()
    expect(api.listTools).toHaveBeenCalledTimes(2)
  })

  it('shows tool calls by default and saves the switch', async () => {
    renderAt('/context-gateway/profiles/p2')
    await screen.findByDisplayValue('Chat')
    fireEvent.click(sectionSwitch('tools'))
    const showCalls = screen.getByRole('switch', {
      name: 'profiles.tools.showCalls.label',
    })
    expect(showCalls.getAttribute('aria-checked')).toBe('true')

    fireEvent.click(showCalls)
    fireEvent.click(saveButton())

    await waitFor(() => expect(api.saveProfile).toHaveBeenCalledTimes(1))
    expect(api.saveProfile.mock.calls[0][2]).toMatchObject({
      gateway_tools: true,
      show_tool_calls: false,
    })
  })

  it('starts category limits from the searched sources', async () => {
    renderAt('/context-gateway/profiles/p2')
    await screen.findByDisplayValue('Chat')
    fireEvent.click(sectionSwitch('recall'))
    fireEvent.click(screen.getAllByText('field.advanced')[0])
    fireEvent.click(
      screen.getByRole('switch', { name: 'profiles.recall.quotas.label' }),
    )
    const events = screen.getByLabelText<HTMLInputElement>(
      'profiles.recall.quotas.categories.events',
    )
    expect(events.value).toBe('3')
    fireEvent.change(events, { target: { value: '5' } })
    fireEvent.click(saveButton())

    await waitFor(() => expect(api.saveProfile).toHaveBeenCalledTimes(1))
    expect(api.saveProfile.mock.calls[0][2].quotas).toEqual({
      events: 5,
      entities: 3,
      preferences: 3,
      experiences: 3,
      resources: 3,
      skills: 3,
    })
  })

  it('creates a new profile once it has a name', async () => {
    renderAt('/context-gateway/profiles/new')
    await screen.findByText('profiles.editor.newTitle')
    expect(saveButton().disabled).toBe(true)
    expect(screen.getByText('profiles.editor.needsName')).toBeTruthy()

    expect(api.listProfiles).not.toHaveBeenCalled()
    fireEvent.change(nameInput(), { target: { value: '  Writing ' } })
    fireEvent.click(saveButton())
    await waitFor(() => expect(api.saveProfile).toHaveBeenCalledTimes(1))
    const [, id, settings] = api.saveProfile.mock.calls[0]
    expect(id).toMatch(/^[0-9a-f]{16}$/)
    expect(settings).toEqual({ ...PROFILE_DEFAULTS, name: 'Writing' })
  })

  it('explains when the profile no longer exists', async () => {
    renderAt('/context-gateway/profiles/gone')
    expect(
      await screen.findByText('profiles.editor.notFound.title'),
    ).toBeTruthy()
  })
})
