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
} from '@tanstack/react-router'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { describeTest } from '#/features/admin/gateway/components/upstreams-actions'
import { UpstreamEditor } from '#/features/admin/gateway/components/upstreams-editor'
import { UpstreamsPage } from '#/features/admin/gateway/components/upstreams-page'
import type * as Api from '#/features/admin/gateway/lib/api'
import type {
  GatewayKey,
  Upstream,
  UpstreamInput,
} from '#/features/admin/gateway/lib/api'
import { GatewayError } from '#/features/admin/gateway/lib/api'
import { gatewayDocsUrl } from '#/features/admin/gateway/lib/client-guides'
import type { Translate } from '#/features/admin/gateway/lib/localize'
import { UPSTREAM_DEFAULTS } from '#/features/admin/gateway/lib/upstream-schema'

const api = vi.hoisted(() => ({
  getAccess: vi.fn(),
  importVlm: vi.fn(),
  listUpstreams: vi.fn(),
  listKeys: vi.fn(),
  saveUpstream: vi.fn(),
  deleteUpstream: vi.fn(),
  testUpstream: vi.fn(),
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))

/** Returns the key, plus the `name` value when one is passed. */
function translate(key: string, values?: Record<string, unknown>): string {
  return typeof values?.name === 'string' ? `${key} ${values.name}` : key
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: translate,
    i18n: { resolvedLanguage: 'en' },
  }),
}))
vi.mock('sonner', () => ({ toast }))
vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({ ready: true, provider: 'opensource' }),
}))

vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'https://ov.example.com',
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
vi.mock('#/features/admin/gateway/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof Api>()),
  ...api,
}))

const openai: Upstream = {
  ...UPSTREAM_DEFAULTS,
  id: 'u1',
  revision: 3,
  name: 'OpenAI',
  base_url: 'https://api.openai.com/v1',
  vendor: 'openai',
  models: ['gpt-5', 'gpt-5-mini', 'gpt-4.1', 'o3'],
  aliases: { fast: 'gpt-5-mini' },
  priority: 5,
  has_api_key: true,
  header_names: ['OpenAI-Organization'],
}
const claude: Upstream = {
  ...UPSTREAM_DEFAULTS,
  id: 'u2',
  revision: 1,
  name: 'Claude',
  protocol: 'anthropic',
  base_url: 'https://api.anthropic.com',
  auth_mode: 'passthrough',
  has_api_key: false,
  header_names: [],
}
const ark: Upstream = {
  ...UPSTREAM_DEFAULTS,
  id: 'u3',
  revision: 1,
  name: 'Ark',
  vendor: 'ark',
  base_url: 'https://ark.cn-beijing.volces.com',
  enabled: false,
  coding_plan: true,
  has_api_key: false,
  header_names: [],
}

function gatewayKey(id: string, upstreamIds: string[]): GatewayKey {
  return {
    id,
    revision: 1,
    name: id,
    policy_id: 'p1',
    upstream_ids: upstreamIds,
    models: [],
    user_id: 'alice',
    prefix: 'ovgw_abc',
    created_at: 1_700_000_000,
  }
}

const FORBIDDEN_FIELDS = ['id', 'revision', 'has_api_key', 'header_names']

/** The body of the last `saveUpstream` call, checked to be a complete input. */
function lastSavedBody(): UpstreamInput {
  const body = api.saveUpstream.mock.lastCall?.[2] as UpstreamInput
  for (const field of FORBIDDEN_FIELDS) expect(body).not.toHaveProperty(field)
  expect(Object.keys(body).sort()).toEqual(
    Object.keys(UPSTREAM_DEFAULTS).sort(),
  )
  return body
}

function EditorRoute() {
  const { upstreamId } = useParams({ strict: false })
  return <UpstreamEditor upstreamId={upstreamId ?? ''} />
}

function renderAt(path: string) {
  const root = createRootRoute({ component: Outlet })
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({
        getParentRoute: () => root,
        path: '/admin/gateway/upstreams',
        component: UpstreamsPage,
      }),
      createRoute({
        getParentRoute: () => root,
        path: '/admin/gateway/upstreams/$upstreamId',
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

/** The radio inside the protocol card titled with `protocol`'s label. */
const protocolRadio = (protocol: string) => {
  const radio = screen
    .getByText(`enums.protocol.${protocol}`)
    .closest('label')
    ?.querySelector('[role="radio"]')
  if (!radio) throw new Error(`no radio for ${protocol}`)
  return radio
}

const baseUrlInput = () =>
  screen.getByLabelText<HTMLInputElement>('upstreams.form.baseUrl.label')

const useDefaultButton = () =>
  screen.queryByRole('button', { name: /upstreams\.form\.baseUrl\.useDefault/ })

async function chooseVendor(vendor: string) {
  fireEvent.click(
    screen.getByRole('combobox', { name: 'upstreams.form.vendor.label' }),
  )
  const option = await screen.findByRole('option', {
    name: `enums.vendor.${vendor}`,
  })
  // Base UI selects only the highlighted option; hovering highlights it.
  fireEvent.mouseMove(option)
  fireEvent.click(option)
}

const rowOf = (name: string) => {
  const row = screen.getByText(name).closest('tr')
  if (!row) throw new Error(`no row for ${name}`)
  return within(row)
}

/** Hrefs of every link with this text (Base UI gives button links the button role). */
const hrefsOf = (text: string) =>
  screen
    .getAllByText(text)
    .map((node) => node.closest('a')?.getAttribute('href'))

beforeEach(() => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  for (const fn of [...Object.values(api), ...Object.values(toast)]) {
    fn.mockReset()
  }
  api.getAccess.mockResolvedValue({
    managed: false,
    can_write_objects: true,
    can_import_vlm: false,
    can_issue_for_others: true,
    role: 'admin',
    account_id: 'acme',
    user_id: 'admin',
    warnings: [],
  })
  api.listUpstreams.mockResolvedValue([openai, claude, ark])
  api.listKeys.mockResolvedValue([
    gatewayKey('k1', ['u1']),
    gatewayKey('k2', ['u1', 'u2']),
  ])
  api.saveUpstream.mockImplementation(
    (_connection, id: string, input: UpstreamInput) =>
      Promise.resolve({ ...input, id, revision: 1 }),
  )
})
afterEach(cleanup)

describe('UpstreamsPage', () => {
  it('summarizes each upstream', async () => {
    renderAt('/admin/gateway/upstreams')
    await screen.findByText('OpenAI')

    const first = rowOf('OpenAI')
    expect(first.getByText('api.openai.com')).toBeTruthy()
    expect(first.getByText('gpt-5')).toBeTruthy()
    expect(first.queryByText('o3')).toBeNull()
    expect(first.getByText('upstreams.models.more')).toBeTruthy()
    expect(first.getByText('upstreams.models.aliases')).toBeTruthy()
    await waitFor(() =>
      expect(first.getByText('upstreams.usedBy')).toBeTruthy(),
    )

    const passthrough = rowOf('Claude')
    expect(passthrough.getByText('upstreams.models.any')).toBeTruthy()
    expect(passthrough.getByText('enums.authMode.passthrough')).toBeTruthy()
    expect(
      passthrough.queryByText('upstreams.credentials.keyMissing'),
    ).toBeNull()

    const blocked = rowOf('Ark')
    expect(blocked.getByText('upstreams.credentials.keyMissing')).toBeTruthy()
    expect(blocked.getByText('upstreams.credentials.blocked')).toBeTruthy()
    expect(blocked.getByText('upstreams.notUsed')).toBeTruthy()
    expect(hrefsOf('docs.link')).toEqual([gatewayDocsUrl('upstreams', 'en')])
  })

  it('explains an empty account', async () => {
    api.listUpstreams.mockResolvedValue([])
    renderAt('/admin/gateway/upstreams')
    expect(await screen.findByText('upstreams.empty.title')).toBeTruthy()
    expect(screen.getAllByText('upstreams.add').length).toBe(2)
  })

  it('saves the complete upstream when switched off and rolls back on failure', async () => {
    let reject: (error: unknown) => void = () => {}
    api.saveUpstream.mockReturnValue(
      new Promise((_resolve, fail) => {
        reject = fail
      }),
    )
    renderAt('/admin/gateway/upstreams')
    const toggle = await screen.findByRole('switch', {
      name: 'upstreams.toggle.disable OpenAI',
    })
    fireEvent.click(toggle)

    await waitFor(() => expect(api.saveUpstream).toHaveBeenCalledTimes(1))
    expect(api.saveUpstream.mock.lastCall?.[1]).toBe('u1')
    expect(lastSavedBody()).toMatchObject({
      enabled: false,
      api_key: '',
      headers: { 'OpenAI-Organization': '' },
      models: openai.models,
      aliases: openai.aliases,
      priority: 5,
    })
    expect(toggle.getAttribute('aria-checked')).toBe('false')

    reject(new GatewayError('Upstream save failed', 500))
    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    await waitFor(() =>
      expect(toggle.getAttribute('aria-checked')).toBe('true'),
    )
  })

  it('rolls back only the row whose save failed', async () => {
    const saves: Array<(error: unknown) => void> = []
    api.saveUpstream.mockImplementation(
      () =>
        new Promise((_resolve, fail) => {
          saves.push(fail)
        }),
    )
    renderAt('/admin/gateway/upstreams')
    const first = await screen.findByRole('switch', {
      name: 'upstreams.toggle.disable OpenAI',
    })
    const second = screen.getByRole('switch', {
      name: 'upstreams.toggle.disable Claude',
    })
    fireEvent.click(first)
    fireEvent.click(second)
    await waitFor(() => expect(api.saveUpstream).toHaveBeenCalledTimes(2))
    // Both saves are running, so neither switch can be flipped again.
    expect(first.hasAttribute('data-disabled')).toBe(true)
    expect(second.hasAttribute('data-disabled')).toBe(true)

    saves[0](new GatewayError('Upstream save failed', 500))
    await waitFor(() => expect(first.getAttribute('aria-checked')).toBe('true'))
    expect(first.hasAttribute('data-disabled')).toBe(false)
    expect(second.getAttribute('aria-checked')).toBe('false')
    expect(second.hasAttribute('data-disabled')).toBe(true)
  })

  it('shows the test result in the row and blocks tests without a stored key', async () => {
    api.testUpstream.mockImplementation(
      (_connection, _id, model: string | undefined) =>
        Promise.resolve({
          ok: true,
          status: 200,
          kind: model ? 'model' : 'connection',
          reason: 'passed',
        }),
    )
    // Stored before append_endpoint_path existed: the list returns no such field.
    const { append_endpoint_path: _, ...legacy } = openai
    api.listUpstreams.mockResolvedValue([legacy as Upstream, claude, ark])
    renderAt('/admin/gateway/upstreams')
    await screen.findByText('OpenAI')

    fireEvent.click(
      rowOf('OpenAI').getByRole('button', { name: 'upstreams.test.action' }),
    )
    await waitFor(() =>
      expect(api.testUpstream).toHaveBeenCalledWith(expect.anything(), 'u1'),
    )
    await screen.findByText('upstreams.test.result.connected')
    expect(hrefsOf('docs.learnMore')).toEqual([
      gatewayDocsUrl('testUpstream', 'en'),
    ])
    fireEvent.click(
      screen.getByRole('button', {
        name: 'upstreams.test.dialog.run',
      }),
    )
    await waitFor(() =>
      expect(api.testUpstream).toHaveBeenCalledWith(
        expect.anything(),
        'u1',
        'gpt-5',
      ),
    )
    expect(
      (await screen.findAllByText('upstreams.test.result.ok')).length,
    ).toBeGreaterThanOrEqual(1)
    fireEvent.click(screen.getByRole('button', { name: 'actions.cancel' }))

    for (const name of ['Claude', 'Ark']) {
      const button = rowOf(name).getByRole('button', {
        name: 'upstreams.test.action',
      })
      expect(button.hasAttribute('disabled')).toBe(true)
    }
  })

  it('tests a model directly on an exact endpoint', async () => {
    api.testUpstream.mockResolvedValue({
      ok: true,
      status: 200,
      kind: 'model',
      reason: 'passed',
    })
    api.listUpstreams.mockResolvedValue([
      { ...openai, append_endpoint_path: false },
    ])
    renderAt('/admin/gateway/upstreams')
    await screen.findByText('OpenAI')

    fireEvent.click(
      rowOf('OpenAI').getByRole('button', { name: 'upstreams.test.action' }),
    )
    await screen.findByText('upstreams.test.dialog.exactEndpoint')
    expect(api.testUpstream).not.toHaveBeenCalled()
    fireEvent.click(
      screen.getByRole('button', { name: 'upstreams.test.dialog.run' }),
    )
    await waitFor(() =>
      expect(api.testUpstream).toHaveBeenCalledWith(
        expect.anything(),
        'u1',
        'gpt-5',
      ),
    )
  })

  it('keeps model testing disabled when the connection test fails', async () => {
    api.testUpstream.mockResolvedValue({
      ok: false,
      kind: 'connection',
      reason: 'upstream_unavailable',
    })
    renderAt('/admin/gateway/upstreams')
    await screen.findByText('OpenAI')

    fireEvent.click(
      rowOf('OpenAI').getByRole('button', { name: 'upstreams.test.action' }),
    )

    await screen.findByText('upstreams.test.result.unreachable')
    expect(
      screen
        .getByRole('button', { name: 'upstreams.test.dialog.run' })
        .hasAttribute('disabled'),
    ).toBe(true)
    expect(api.testUpstream).toHaveBeenCalledTimes(1)
  })

  it.each([
    [
      '{"error":{"message":"Invalid API key"}}',
      { error: { message: 'Invalid API key' } },
    ],
    [
      '<html><head><title>Bad Gateway</title><style>body{color:red}</style></head><body><h1>502 Bad Gateway</h1><p>Upstream unavailable</p><script>alert(1)</script></body></html>',
      {
        title: 'Bad Gateway',
        body: '502 Bad Gateway\nUpstream unavailable',
      },
    ],
    ['upstream connection closed', 'upstream connection closed'],
  ])(
    'formats a failed upstream response for display',
    async (body, readableBody) => {
      const response = {
        http_version: 'HTTP/1.1',
        status: 401,
        reason: 'Unauthorized',
        headers: { 'Content-Type': 'application/json' },
        body,
      }
      api.testUpstream
        .mockResolvedValueOnce({
          ok: true,
          status: 200,
          kind: 'connection',
          reason: 'passed',
        })
        .mockResolvedValueOnce({
          ok: false,
          status: 401,
          kind: 'model',
          reason: 'upstream_key_rejected',
          response,
        })
      renderAt('/admin/gateway/upstreams')
      await screen.findByText('OpenAI')

      fireEvent.click(
        rowOf('OpenAI').getByRole('button', { name: 'upstreams.test.action' }),
      )
      await screen.findByText('upstreams.test.result.connected')
      fireEvent.click(
        screen.getByRole('button', {
          name: 'upstreams.test.dialog.run',
        }),
      )

      const label = await screen.findByText('upstreams.test.dialog.response')
      const text = label.parentElement?.querySelector('pre')?.textContent ?? ''
      expect(JSON.parse(text)).toEqual({ ...response, body: readableBody })
    },
  )

  it('deletes an unused upstream after confirmation', async () => {
    api.deleteUpstream.mockResolvedValue({ deleted: true })
    renderAt('/admin/gateway/upstreams')
    await screen.findByText('Ark')

    fireEvent.click(rowOf('Ark').getByRole('button', { name: 'actions.more' }))
    fireEvent.click(await screen.findByText('upstreams.delete.action'))
    expect(await screen.findByText('upstreams.delete.title Ark')).toBeTruthy()
    fireEvent.click(screen.getByText('upstreams.delete.confirm'))

    await waitFor(() => expect(api.deleteUpstream).toHaveBeenCalledTimes(1))
    expect(api.deleteUpstream.mock.lastCall?.[1]).toBe('u3')
    expect(toast.success).toHaveBeenCalledWith('upstreams.toast.deleted Ark')
  })

  it('explains deleting an upstream that keys use', async () => {
    renderAt('/admin/gateway/upstreams')
    await screen.findByText('OpenAI')
    await waitFor(() =>
      expect(rowOf('OpenAI').getByText('upstreams.usedBy')).toBeTruthy(),
    )

    fireEvent.click(
      rowOf('OpenAI').getByRole('button', { name: 'actions.more' }),
    )
    const item = (await screen.findByText('upstreams.delete.action')).closest(
      '[role="menuitem"]',
    )
    expect(item?.getAttribute('aria-disabled')).not.toBe('true')
    expect(screen.getByText('upstreams.delete.blocked')).toBeTruthy()
  })
})

describe('UpstreamEditor', () => {
  it('keeps stored secrets and previews the endpoint while editing', async () => {
    const router = renderAt('/admin/gateway/upstreams/u1')
    const name = await screen.findByLabelText('upstreams.form.name.label')
    expect((name as HTMLInputElement).value).toBe('OpenAI')
    expect(screen.getByText('upstreams.form.apiKey.stored')).toBeTruthy()
    expect(
      screen
        .getByLabelText('upstreams.form.apiKey.label', { exact: false })
        .getAttribute('placeholder'),
    ).toBe('field.storedSecret')
    expect(screen.getByDisplayValue('OpenAI-Organization')).toBeTruthy()
    // The API key and the stored header value both read "leave blank to keep".
    expect(screen.getAllByPlaceholderText('field.storedSecret')).toHaveLength(2)
    expect(
      screen.getByText('https://api.openai.com/v1/chat/completions'),
    ).toBeTruthy()
    // One docs link per section, after its description.
    expect(hrefsOf('docs.learnMore')).toEqual(
      (['endpoint', 'credentials', 'models', 'routing'] as const).map(
        (section) => gatewayDocsUrl(section, 'en'),
      ),
    )

    const save = screen.getByRole('button', { name: 'actions.save' })
    expect(save.hasAttribute('disabled')).toBe(true)

    fireEvent.change(screen.getByLabelText('upstreams.form.name.label'), {
      target: { value: 'OpenAI EU' },
    })
    await waitFor(() => expect(save.hasAttribute('disabled')).toBe(false))
    fireEvent.click(save)

    await waitFor(() => expect(api.saveUpstream).toHaveBeenCalledTimes(1))
    expect(api.saveUpstream.mock.lastCall?.[1]).toBe('u1')
    expect(lastSavedBody()).toMatchObject({
      name: 'OpenAI EU',
      api_key: '',
      headers: { 'OpenAI-Organization': '' },
      priority: 5,
    })
    expect(toast.success).toHaveBeenCalledWith(
      'upstreams.toast.saved OpenAI EU',
    )
    await waitFor(() =>
      expect(router.state.location.pathname).toBe('/admin/gateway/upstreams'),
    )
  })

  it('can send requests to the exact base URL without a protocol path', async () => {
    renderAt('/admin/gateway/upstreams/u1')
    await screen.findByLabelText('upstreams.form.name.label')
    const toggle = screen.getByRole('switch', {
      name: 'upstreams.form.appendEndpointPath.label',
    })
    expect(toggle.getAttribute('aria-checked')).toBe('true')

    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    expect(screen.getByText('https://api.openai.com/v1')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'actions.save' }))
    await waitFor(() => expect(api.saveUpstream).toHaveBeenCalledTimes(1))
    expect(lastSavedBody().append_endpoint_path).toBe(false)
  })

  it('asks for the API key and header values again after the address changes', async () => {
    renderAt('/admin/gateway/upstreams/u1')
    await screen.findByLabelText('upstreams.form.name.label')
    const save = screen.getByRole('button', { name: 'actions.save' })
    fireEvent.change(screen.getByLabelText('upstreams.form.baseUrl.label'), {
      target: { value: 'https://llm.example.com' },
    })
    expect(
      screen.getByText('https://llm.example.com/chat/completions'),
    ).toBeTruthy()
    expect(
      screen.getByText('upstreams.form.apiKey.addressChanged'),
    ).toBeTruthy()
    expect(
      screen.getByText('upstreams.form.headers.addressChanged'),
    ).toBeTruthy()
    expect(save.hasAttribute('disabled')).toBe(true)

    fireEvent.change(
      screen.getByLabelText('upstreams.form.apiKey.label', { exact: false }),
      { target: { value: 'sk-new' } },
    )
    fireEvent.change(
      screen.getByPlaceholderText('upstreams.form.headers.value'),
      { target: { value: 'org-1' } },
    )
    await waitFor(() => expect(save.hasAttribute('disabled')).toBe(false))
    fireEvent.click(save)
    await waitFor(() =>
      expect(lastSavedBody()).toMatchObject({
        base_url: 'https://llm.example.com',
        api_key: 'sk-new',
        headers: { 'OpenAI-Organization': 'org-1' },
      }),
    )
  })

  it('offers disabling after the server reports keys still use the upstream', async () => {
    api.deleteUpstream.mockRejectedValue(
      new GatewayError('In use', 409, undefined, 'in_use', { key_count: 2 }),
    )
    renderAt('/admin/gateway/upstreams/u1')
    await screen.findByLabelText('upstreams.form.name.label')
    fireEvent.click(
      await screen.findByRole('button', { name: 'upstreams.delete.action' }),
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'upstreams.delete.confirm' }),
    )
    expect(await screen.findByText('upstreams.delete.inUse')).toBeTruthy()
    expect(
      screen
        .getByRole('link', { name: 'actions.viewKeys' })
        .getAttribute('href'),
    ).toBe('/admin/gateway/keys')
    fireEvent.click(
      screen.getByRole('button', { name: 'upstreams.delete.disable' }),
    )
    await waitFor(() => expect(api.saveUpstream).toHaveBeenCalledTimes(1))
    expect(lastSavedBody().enabled).toBe(false)
  })

  it('creates an upstream once the required fields are valid', async () => {
    renderAt('/admin/gateway/upstreams/new')
    expect(await screen.findByText('upstreams.editor.newTitle')).toBeTruthy()
    const create = screen.getByRole('button', {
      name: 'upstreams.editor.create',
    })

    fireEvent.change(screen.getByLabelText('upstreams.form.name.label'), {
      target: { value: '  DeepSeek  ' },
    })
    expect(screen.getByText('upstreams.editor.missing')).toBeTruthy()
    const baseUrl = screen.getByLabelText('upstreams.form.baseUrl.label')
    fireEvent.change(baseUrl, { target: { value: 'api.deepseek.com' } })
    fireEvent.blur(baseUrl)
    expect(screen.getByText('validation.baseUrl')).toBeTruthy()
    expect(create.hasAttribute('disabled')).toBe(true)

    fireEvent.change(baseUrl, { target: { value: 'https://api.deepseek.com' } })
    fireEvent.change(
      screen.getByLabelText('upstreams.form.apiKey.label', { exact: false }),
      { target: { value: 'sk-test' } },
    )
    await waitFor(() => expect(create.hasAttribute('disabled')).toBe(false))
    fireEvent.click(create)

    await waitFor(() => expect(api.saveUpstream).toHaveBeenCalledTimes(1))
    expect(api.saveUpstream.mock.lastCall?.[1]).toMatch(/^[0-9a-f]{16}$/)
    expect(lastSavedBody()).toMatchObject({
      name: 'DeepSeek',
      base_url: 'https://api.deepseek.com',
      api_key: 'sk-test',
      protocol: 'chat',
      enabled: true,
      headers: {},
    })
  })

  it('sends the key in the chosen header, or in the URL placeholder', async () => {
    const azure =
      'https://res.openai.azure.com/openai/deployments/dep/chat/completions?api-version=2024-10-21'
    renderAt('/admin/gateway/upstreams/new')
    await screen.findByText('upstreams.editor.newTitle')
    fireEvent.change(screen.getByLabelText('upstreams.form.name.label'), {
      target: { value: 'Azure' },
    })
    const keyHeader = screen.getByRole('combobox', {
      name: 'upstreams.form.keyHeader.label',
    })
    fireEvent.change(baseUrlInput(), {
      target: { value: 'https://proxy.example.com/v1?key={api_key}' },
    })
    expect(keyHeader.textContent).toContain('upstreams.form.keyHeader.inUrl')
    expect(keyHeader.hasAttribute('data-disabled')).toBe(true)

    fireEvent.change(baseUrlInput(), { target: { value: azure } })
    fireEvent.click(
      screen.getByRole('switch', {
        name: 'upstreams.form.appendEndpointPath.label',
      }),
    )
    fireEvent.change(
      screen.getByLabelText('upstreams.form.apiKey.label', { exact: false }),
      { target: { value: 'azure-key' } },
    )
    fireEvent.click(keyHeader)
    const option = await screen.findByRole('option', {
      name: 'upstreams.form.keyHeader.apiKey',
    })
    fireEvent.mouseMove(option)
    fireEvent.click(option)
    fireEvent.click(
      screen.getByRole('button', { name: 'upstreams.editor.create' }),
    )
    await waitFor(() => expect(api.saveUpstream).toHaveBeenCalledTimes(1))
    expect(lastSavedBody()).toMatchObject({
      base_url: azure,
      append_endpoint_path: false,
      key_header: 'api-key',
    })
  })

  it('shows settings that depend on the provider, key mode and plan', async () => {
    renderAt('/admin/gateway/upstreams/new')
    await screen.findByText('upstreams.editor.newTitle')
    expect(screen.queryByText('upstreams.form.cacheMinTokens.label')).toBeNull()
    expect(screen.queryByText('upstreams.form.codingPlan.warning')).toBeNull()

    fireEvent.click(screen.getByText('upstreams.form.codingPlan.label'))
    expect(screen.getByText('upstreams.form.codingPlan.warning')).toBeTruthy()

    fireEvent.click(
      screen.getByText('upstreams.form.authMode.passthrough.title'),
    )
    await waitFor(() =>
      expect(
        screen.queryByLabelText('upstreams.form.apiKey.label', {
          exact: false,
        }),
      ).toBeNull(),
    )
  })

  it('offers only the protocols the chosen provider supports', async () => {
    renderAt('/admin/gateway/upstreams/new')
    await screen.findByText('upstreams.editor.newTitle')
    expect(protocolRadio('chat').getAttribute('aria-checked')).toBe('true')
    expect(screen.queryByText('upstreams.form.protocol.unsupported')).toBeNull()

    await chooseVendor('anthropic')
    await waitFor(() =>
      expect(protocolRadio('anthropic').getAttribute('aria-checked')).toBe(
        'true',
      ),
    )
    for (const protocol of ['chat', 'responses']) {
      expect(protocolRadio(protocol).getAttribute('aria-disabled')).toBe('true')
    }
    expect(protocolRadio('anthropic').hasAttribute('aria-disabled')).toBe(false)
    // Tabbing into the group lands on the selected card, not a disabled one.
    expect(protocolRadio('anthropic').getAttribute('tabindex')).toBe('0')
    expect(protocolRadio('chat').getAttribute('tabindex')).toBe('-1')
    expect(
      screen.getAllByText('upstreams.form.protocol.unsupported'),
    ).toHaveLength(2)
    expect(baseUrlInput().value).toBe('https://api.anthropic.com')
  })

  it('keeps a supported protocol and replaces an unsupported one', async () => {
    renderAt('/admin/gateway/upstreams/new')
    await screen.findByText('upstreams.editor.newTitle')
    fireEvent.click(screen.getByText('enums.protocol.anthropic'))
    await waitFor(() =>
      expect(protocolRadio('anthropic').getAttribute('aria-checked')).toBe(
        'true',
      ),
    )

    await chooseVendor('deepseek')
    await waitFor(() =>
      expect(baseUrlInput().value).toBe('https://api.deepseek.com/anthropic'),
    )
    expect(protocolRadio('anthropic').getAttribute('aria-checked')).toBe('true')
    expect(screen.queryByText('upstreams.form.protocol.unsupported')).toBeNull()

    await chooseVendor('openai')
    await waitFor(() =>
      expect(protocolRadio('chat').getAttribute('aria-checked')).toBe('true'),
    )
    expect(protocolRadio('anthropic').getAttribute('aria-disabled')).toBe(
      'true',
    )
    expect(baseUrlInput().value).toBe('https://api.openai.com/v1')
  })

  it('preselects reasoning restore by provider and saves only a deviation', async () => {
    renderAt('/admin/gateway/upstreams/new')
    await screen.findByText('upstreams.editor.newTitle')
    const toggle = () =>
      screen.getByRole('switch', {
        name: 'upstreams.form.replayReasoning.label',
      })
    expect(toggle().getAttribute('aria-checked')).toBe('false')

    await chooseVendor('deepseek')
    await waitFor(() =>
      expect(toggle().getAttribute('aria-checked')).toBe('true'),
    )
    fireEvent.click(toggle())
    expect(toggle().getAttribute('aria-checked')).toBe('false')

    fireEvent.change(screen.getByLabelText('upstreams.form.name.label'), {
      target: { value: 'DeepSeek' },
    })
    fireEvent.change(
      screen.getByLabelText('upstreams.form.apiKey.label', { exact: false }),
      { target: { value: 'sk-test' } },
    )
    const create = screen.getByRole('button', {
      name: 'upstreams.editor.create',
    })
    await waitFor(() => expect(create.hasAttribute('disabled')).toBe(false))
    fireEvent.click(create)
    await waitFor(() => expect(api.saveUpstream).toHaveBeenCalledTimes(1))
    expect(lastSavedBody()).toMatchObject({
      vendor: 'deepseek',
      replay_reasoning: false,
    })
  })

  it("fills in the provider's default base URL for each protocol", async () => {
    renderAt('/admin/gateway/upstreams/new')
    await screen.findByText('upstreams.editor.newTitle')
    expect(baseUrlInput().value).toBe('')

    await chooseVendor('openai')
    await waitFor(() =>
      expect(baseUrlInput().value).toBe('https://api.openai.com/v1'),
    )
    expect(useDefaultButton()).toBeNull()

    await chooseVendor('deepseek')
    await waitFor(() =>
      expect(baseUrlInput().value).toBe('https://api.deepseek.com'),
    )
    fireEvent.click(screen.getByText('enums.protocol.anthropic'))
    await waitFor(() =>
      expect(baseUrlInput().value).toBe('https://api.deepseek.com/anthropic'),
    )
    fireEvent.click(screen.getByText('enums.protocol.chat'))
    await waitFor(() =>
      expect(baseUrlInput().value).toBe('https://api.deepseek.com'),
    )

    await chooseVendor('generic')
    await waitFor(() => expect(baseUrlInput().value).toBe(''))
  })

  it('flags a base URL cleared by a provider switch only once visited', async () => {
    renderAt('/admin/gateway/upstreams/new')
    await screen.findByText('upstreams.editor.newTitle')

    await chooseVendor('openai')
    await waitFor(() =>
      expect(baseUrlInput().value).toBe('https://api.openai.com/v1'),
    )
    await chooseVendor('generic')
    await waitFor(() => expect(baseUrlInput().value).toBe(''))
    expect(baseUrlInput().getAttribute('aria-invalid')).toBe('false')
    expect(screen.queryByText('validation.required')).toBeNull()

    fireEvent.blur(baseUrlInput())
    expect(screen.getByText('validation.required')).toBeTruthy()
  })

  it('keeps a base URL the admin entered and offers the default', async () => {
    api.listUpstreams.mockResolvedValue([
      { ...openai, base_url: 'https://proxy.example.com/v1' },
    ])
    renderAt('/admin/gateway/upstreams/u1')
    await screen.findByLabelText('upstreams.form.name.label')
    expect(baseUrlInput().value).toBe('https://proxy.example.com/v1')

    await chooseVendor('byteplus')
    await waitFor(() =>
      expect(
        screen.getByText('upstreams.form.cacheMinTokens.label'),
      ).toBeTruthy(),
    )
    expect(baseUrlInput().value).toBe('https://proxy.example.com/v1')

    fireEvent.click(useDefaultButton() as HTMLElement)
    await waitFor(() =>
      expect(baseUrlInput().value).toBe(
        'https://ark.ap-southeast.bytepluses.com/api/v3',
      ),
    )
    expect(useDefaultButton()).toBeNull()
    expect(
      screen.getByText(
        'https://ark.ap-southeast.bytepluses.com/api/v3/chat/completions',
      ),
    ).toBeTruthy()
  })

  it('flags a stored protocol the provider does not offer and blocks saving', async () => {
    api.listUpstreams.mockResolvedValue([
      { ...openai, id: 'u4', name: 'Mismatch', protocol: 'anthropic' },
    ])
    renderAt('/admin/gateway/upstreams/u4')
    await screen.findByLabelText('upstreams.form.name.label')

    // Shown at once and left as stored, not switched behind the user's back.
    expect(screen.getByText('validation.protocolUnsupported')).toBeTruthy()
    expect(protocolRadio('anthropic').getAttribute('aria-checked')).toBe('true')

    fireEvent.change(screen.getByLabelText('upstreams.form.name.label'), {
      target: { value: 'Renamed' },
    })
    const save = screen.getByRole('button', { name: 'actions.save' })
    expect(screen.getByText('upstreams.editor.missing')).toBeTruthy()
    expect(save.hasAttribute('disabled')).toBe(true)

    fireEvent.click(screen.getByText('enums.protocol.chat'))
    await waitFor(() => expect(save.hasAttribute('disabled')).toBe(false))
    expect(screen.queryByText('validation.protocolUnsupported')).toBeNull()
  })

  it('reports an upstream that no longer exists', async () => {
    renderAt('/admin/gateway/upstreams/gone')
    expect(
      await screen.findByText('upstreams.editor.notFound.title'),
    ).toBeTruthy()
  })
})

describe('describeTest', () => {
  const t = translate as unknown as Translate

  it.each([
    [{ ok: true, status: 200 }, 'success', 'upstreams.test.explain.ok'],
    [
      {
        ok: true,
        status: 200,
        kind: 'connection' as const,
        reason: 'passed',
      },
      'success',
      'upstreams.test.explain.connected',
    ],
    [
      {
        ok: true,
        status: 404,
        kind: 'connection' as const,
        reason: 'models_unavailable',
      },
      'neutral',
      'upstreams.test.explain.connectionUnverified',
    ],
    [{ ok: false, status: 401 }, 'danger', 'upstreams.test.explain.auth'],
    [
      { ok: false, status: 429, reason: 'rate_limited' },
      'neutral',
      'upstreams.test.explain.rateLimited',
    ],
    [
      {
        ok: false,
        status: 404,
        reason: 'model_request_rejected',
        model: 'missing',
      },
      'danger',
      'upstreams.test.explain.requestRejected',
    ],
    [
      { ok: false, status: 200, reason: 'invalid_response' },
      'danger',
      'upstreams.test.explain.invalidResponse',
    ],
    [
      { ok: false, status: 200, reason: 'upstream_key_rejected' },
      'danger',
      'upstreams.test.explain.auth',
    ],
    [{ ok: false, status: 500 }, 'danger', 'upstreams.test.explain.status'],
    [
      { ok: false, reason: 'upstream_key_missing' },
      'danger',
      'errors.reasons.upstream_key_missing',
    ],
    [
      { ok: false, reason: 'upstream_unavailable' },
      'danger',
      'upstreams.test.explain.unreachable',
    ],
  ])('explains %j', (result, tone, explanation) => {
    expect(describeTest(t, { result })).toMatchObject({ tone, explanation })
  })

  it('explains a failed call with the gateway message', () => {
    const summary = describeTest(t, {
      error: new GatewayError(
        'Upstream API key is missing',
        401,
        undefined,
        'upstream_key_missing',
      ),
    })
    expect(summary).toEqual({
      tone: 'danger',
      label: 'upstreams.test.result.error',
      explanation: 'errors.reasons.upstream_key_missing',
    })
  })
})

describe('upstream access and import', () => {
  function readOnly() {
    api.getAccess.mockResolvedValue({
      managed: true,
      can_write_objects: false,
      can_import_vlm: false,
      can_issue_for_others: false,
      role: 'user',
      account_id: 'acme',
      user_id: 'alice',
      warnings: [],
    })
  }

  it('shows read-only list details without write actions or model tests', async () => {
    readOnly()
    renderAt('/admin/gateway/upstreams')
    await screen.findByText('OpenAI')
    expect(screen.queryByText('upstreams.add')).toBeNull()
    expect(screen.queryByText('upstreams.import.action')).toBeNull()
    expect(screen.queryByText('actions.edit')).toBeNull()
    expect(screen.queryByRole('button', { name: 'actions.more' })).toBeNull()
    const toggle = rowOf('OpenAI').getByRole('switch')
    expect(toggle.hasAttribute('data-disabled')).toBe(true)
    fireEvent.click(toggle)
    expect(api.saveUpstream).not.toHaveBeenCalled()
    expect(
      rowOf('OpenAI').queryByRole('button', {
        name: 'upstreams.test.action',
      }),
    ).toBeNull()
  })

  it('disables read-only editor controls, hides credentials, and tests outside the fieldset', async () => {
    readOnly()
    api.listUpstreams.mockResolvedValue([
      { ...openai, header_names: undefined, coding_plan: true },
    ])
    api.testUpstream.mockResolvedValue({
      ok: true,
      status: 200,
      reason: 'passed',
    })
    renderAt('/admin/gateway/upstreams/u1')
    expect(
      await screen.findByText('upstreams.readOnly.managedKey'),
    ).toBeTruthy()
    expect(screen.getByText('upstreams.readOnly.codingBlocked')).toBeTruthy()
    expect(
      screen.queryByLabelText('upstreams.form.apiKey.label', { exact: false }),
    ).toBeNull()
    expect(screen.queryByText('upstreams.form.headers.label')).toBeNull()
    expect(screen.queryByRole('button', { name: 'actions.save' })).toBeNull()
    expect(
      screen.queryByRole('button', { name: 'upstreams.delete.action' }),
    ).toBeNull()
    expect(screen.getByRole('combobox').hasAttribute('disabled')).toBe(true)
    for (const control of screen.getAllByRole('switch'))
      expect(control.hasAttribute('data-disabled')).toBe(true)
    expect(
      screen.getByLabelText<HTMLInputElement>('upstreams.form.models.label')
        .disabled,
    ).toBe(true)
    expect(
      screen.getByLabelText<HTMLInputElement>('upstreams.form.name.label')
        .disabled,
    ).toBe(true)
    expect(
      screen.queryByRole('button', {
        name: 'upstreams.test.connection',
      }),
    ).toBeNull()
    expect(api.testUpstream).not.toHaveBeenCalled()
  })

  it('blocks the direct create URL for read-only users', async () => {
    readOnly()
    renderAt('/admin/gateway/upstreams/new')
    expect(
      (await screen.findByText('upstreams.editor.back'))
        .closest('a')
        ?.getAttribute('href'),
    ).toBe('/admin/gateway/upstreams')
    expect(screen.queryByLabelText('upstreams.form.name.label')).toBeNull()
  })

  it('warns about a stored key after editing the address and offers a path correction', async () => {
    renderAt('/admin/gateway/upstreams/u1')
    await screen.findByLabelText('upstreams.form.name.label')
    fireEvent.change(baseUrlInput(), {
      target: { value: 'https://other.example' },
    })
    expect(
      screen.getByText('upstreams.form.apiKey.addressChanged'),
    ).toBeTruthy()
    expect(screen.getByText('upstreams.form.baseUrl.missingPath')).toBeTruthy()
    fireEvent.click(
      screen.getByRole('button', {
        name: 'upstreams.form.baseUrl.correctPath',
      }),
    )
    expect(baseUrlInput().value).toBe('https://other.example/v1')
    fireEvent.change(
      screen.getByLabelText('upstreams.form.apiKey.label', { exact: false }),
      { target: { value: 'replacement' } },
    )
    expect(
      screen.queryByText('upstreams.form.apiKey.addressChanged'),
    ).toBeNull()
  })

  it('shows import results, confirmation links and skipped reasons, then dismisses the notice', async () => {
    api.getAccess.mockResolvedValue({
      can_write_objects: true,
      can_import_vlm: true,
    })
    api.importVlm.mockResolvedValue({
      imported: [{ id: 'vlm-a', name: 'A' }],
      needs_confirmation: [{ id: 'vlm-coding', name: 'Coding account' }],
      skipped: [
        { id: 'oauth', reason: 'openai_codex' },
        { id: 'lite', reason: 'litellm_no_api_base' },
      ],
    })
    renderAt('/admin/gateway/upstreams')
    fireEvent.click(
      await screen.findByRole('button', { name: 'upstreams.import.action' }),
    )
    expect(await screen.findByText('upstreams.import.summary')).toBeTruthy()
    expect(
      screen
        .getByRole('link', { name: 'upstreams.import.confirm' })
        .getAttribute('href'),
    ).toBe('/admin/gateway/upstreams/vlm-coding')
    expect(
      screen.getByText('oauth: upstreams.import.reasons.openai_codex'),
    ).toBeTruthy()
    expect(
      screen.getByText('lite: upstreams.import.reasons.litellm_no_api_base'),
    ).toBeTruthy()
    expect(screen.getByText('upstreams.import.repeat')).toBeTruthy()
    // Skipped credentials link the import docs.
    expect(hrefsOf('docs.learnMore')).toEqual([
      gatewayDocsUrl('importModels', 'en'),
    ])
    fireEvent.click(
      screen.getByRole('button', { name: 'upstreams.import.dismiss' }),
    )
    expect(screen.queryByText('upstreams.import.summary')).toBeNull()
    expect(api.importVlm).toHaveBeenCalledTimes(1)
  })

  it('lists only the skipped credentials when an import saves nothing', async () => {
    api.getAccess.mockResolvedValue({
      can_write_objects: true,
      can_import_vlm: true,
    })
    api.importVlm.mockResolvedValue({
      imported: [],
      needs_confirmation: [],
      skipped: [{ id: 'oauth', reason: 'openai_codex' }],
    })
    renderAt('/admin/gateway/upstreams')
    fireEvent.click(
      await screen.findByRole('button', { name: 'upstreams.import.action' }),
    )
    expect(
      await screen.findByText('oauth: upstreams.import.reasons.openai_codex'),
    ).toBeTruthy()
    expect(screen.queryByText('upstreams.import.summary')).toBeNull()
    expect(screen.queryByText('upstreams.import.nothing')).toBeNull()
    expect(screen.queryByText('upstreams.import.repeat')).toBeNull()
  })

  it('offers manual setup when the server cannot import VLM settings', async () => {
    api.getAccess.mockResolvedValue({
      can_write_objects: true,
      can_import_vlm: true,
    })
    api.importVlm.mockRejectedValue(
      new GatewayError('Unavailable', 409, undefined, 'vlm_import_unavailable'),
    )
    renderAt('/admin/gateway/upstreams')
    fireEvent.click(
      await screen.findByRole('button', { name: 'upstreams.import.action' }),
    )
    expect(
      await screen.findByText('errors.reasons.vlm_import_unavailable'),
    ).toBeTruthy()
    expect(screen.getAllByText('upstreams.add')).toHaveLength(2)
  })

  it('hides a forbidden import entry once /access is refreshed', async () => {
    api.getAccess
      .mockResolvedValueOnce({ can_write_objects: true, can_import_vlm: true })
      .mockResolvedValue({ can_write_objects: true, can_import_vlm: false })
    api.importVlm.mockRejectedValue(
      new GatewayError('Forbidden', 403, undefined, 'vlm_import_forbidden'),
    )
    renderAt('/admin/gateway/upstreams')
    fireEvent.click(
      await screen.findByRole('button', { name: 'upstreams.import.action' }),
    )
    expect(
      await screen.findByText('errors.reasons.vlm_import_forbidden'),
    ).toBeTruthy()
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'upstreams.import.action' }),
      ).toBeNull(),
    )
    expect(api.getAccess).toHaveBeenCalledTimes(2)
  })
})
