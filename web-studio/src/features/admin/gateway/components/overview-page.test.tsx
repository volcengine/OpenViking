// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import {
  cleanup,
  configure,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { toast } from 'sonner'
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest'

import i18n from '#/i18n'

import { OverviewPage } from '#/features/admin/gateway/components/overview-page'
import type * as Api from '#/features/admin/gateway/lib/api'
import type {
  CacheAlerts,
  CacheMiss,
  GatewayAccess,
  IssuedKey,
  LogRecord,
  Overview,
  Profile,
  Upstream,
} from '#/features/admin/gateway/lib/api'
import { GatewayError } from '#/features/admin/gateway/lib/api'
import { gatewayDocsUrl } from '#/features/admin/gateway/lib/client-guides'
import { PROFILE_DEFAULTS } from '#/features/admin/gateway/lib/profile-schema'
import { UPSTREAM_DEFAULTS } from '#/features/admin/gateway/lib/upstream-schema'

const api = vi.hoisted(() => ({
  getAccess: vi.fn(),
  getOverview: vi.fn(),
  listLogs: vi.fn(),
  listUpstreams: vi.fn(),
  listProfiles: vi.fn(),
  listKeys: vi.fn(),
  saveProfile: vi.fn(),
  importVlm: vi.fn(),
  issueKey: vi.fn(),
  listKeyUsers: vi.fn(),
  getConnectionInfo: vi.fn(),
}))
const host = vi.hoisted(() => ({ serverMode: 'api_key', gatewayHost: false }))

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
    connectionRole: 'admin',
    isConnectionRoleLoading: false,
    serverMode: host.serverMode,
    isGatewayHost: host.gatewayHost,
  }),
}))
vi.mock('#/features/admin/gateway/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof Api>()),
  ...api,
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const NOW = Date.now() / 1000

const NO_CACHE = {
  requests: 0,
  input_tokens: 0,
  cached_tokens: 0,
  cache_hit_ratio: 0,
}

const EMPTY_OVERVIEW: Overview = {
  requests: 0,
  last_request_at: null,
  output_tokens: 0,
  cache: { first_call: NO_CACHE, continuation: NO_CACHE },
  degradations: {},
  openviking: {
    status: 'ok',
    healthy: true,
    version: '0.4.20',
    auth_mode: 'api_key',
  },
  recall_count: 0,
  recall_requests: 0,
  recall_ms: 0,
  capture_issues: { retrying: 0, paused: 0 },
  sample_limit: 10000,
  log_retention_days: 30,
  cache_alerts: null,
}

const BUSY_OVERVIEW: Overview = {
  requests: 1234,
  last_request_at: NOW - 120,
  output_tokens: 56000,
  cache: {
    first_call: {
      requests: 1234,
      input_tokens: 1000,
      cached_tokens: 920,
      cache_hit_ratio: 0.92,
    },
    continuation: {
      requests: 3,
      input_tokens: 100,
      cached_tokens: 50,
      cache_hit_ratio: 0.5,
    },
  },
  degradations: { plugin_present: 2, upstream_changed: 5 },
  openviking: { status: 'degraded', reason: 'openviking_unavailable' },
  recall_count: 42,
  recall_requests: 40,
  recall_ms: 820,
  capture_issues: { retrying: 1, paused: 2 },
  sample_limit: 10000,
  log_retention_days: 7,
  cache_alerts: null,
}

const LOGS: LogRecord[] = [
  {
    time: NOW - 60,
    request_id: 'r1',
    kind: 'user',
    model: 'claude-sonnet-4-5',
    status: 200,
    recall_count: 3,
    recall_ms: 410,
    replay_hits: 4,
  },
  {
    time: NOW - 90,
    kind: 'capture',
    session: 's1',
    protocol: 'anthropic',
    capture_status: 'paused',
    capture_reason: 'openviking_unavailable',
  },
  {
    time: NOW - 100,
    request_id: 'r2',
    kind: 'continuation',
    model: 'gpt-5',
    status: 502,
    degradation: 'upstream_changed',
  },
]

const UPSTREAMS = [
  { id: 'u1', name: 'OpenAI production' },
  { id: 'u2', name: 'Kimi relay' },
] as Upstream[]

function cacheMiss(fields: Partial<CacheMiss>): CacheMiss {
  return {
    upstream_id: 'u1',
    model: 'gpt-5',
    streak: 5,
    last_at: NOW - 60,
    ...fields,
  }
}

function cacheMisses(
  fields: Partial<NonNullable<CacheAlerts['misses']>>,
): CacheAlerts['misses'] {
  return {
    conversations: 1,
    streak: 5,
    input_tokens: 50000,
    no_writes: false,
    recent: [cacheMiss({})],
    ...fields,
  }
}

/** Pages the overview links to; links only get an href for known routes. */
const LINK_TARGETS = [
  '/admin/gateway/upstreams',
  '/admin/gateway/upstreams/$upstreamId',
  '/admin/gateway/profiles',
  '/admin/gateway/profiles/$profileId',
  '/admin/gateway/keys',
  '/admin/gateway/requests',
  '/admin/gateway/connect',
]

/** Href of a button-styled link (Base UI gives those the button role). */
function hrefOf(text: string) {
  return screen.getByText(text).closest('a')?.getAttribute('href')
}

/** Hrefs of every link with this text, in page order. */
function hrefsOf(text: string) {
  return screen
    .getAllByText(text)
    .map((node) => node.closest('a')?.getAttribute('href'))
}

function renderOverview() {
  const root = createRootRoute({ component: Outlet })
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({
        getParentRoute: () => root,
        path: '/admin/gateway',
        component: OverviewPage,
      }),
      ...LINK_TARGETS.map((path) =>
        createRoute({ getParentRoute: () => root, path }),
      ),
    ]),
    history: createMemoryHistory({ initialEntries: ['/admin/gateway'] }),
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

beforeAll(async () => {
  await i18n.changeLanguage('en')
  // jsdom applies no CSS, so queries would also find the copies that tables
  // show only below `md`; leave those out and test the desktop layout.
  configure({ defaultIgnore: 'script, style, .md\\:hidden, .md\\:hidden *' })
})
afterAll(async () => {
  await i18n.changeLanguage('en')
})
beforeEach(() => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  for (const mock of Object.values(api)) mock.mockReset()
  host.serverMode = 'api_key'
  host.gatewayHost = false
  api.getConnectionInfo.mockResolvedValue({
    base_url: 'http://gateway.test:1933',
    public_url_configured: true,
  })
  api.getAccess.mockResolvedValue({
    managed: false,
    can_write_objects: true,
    can_import_vlm: true,
    can_issue_for_others: true,
    role: 'admin',
    account_id: 'acme',
    user_id: 'admin',
    warnings: [],
  })
  api.listLogs.mockResolvedValue([])
  api.listUpstreams.mockResolvedValue([])
  api.listProfiles.mockResolvedValue([])
  api.listKeys.mockResolvedValue([])
})
afterEach(() => {
  cleanup()
  vi.mocked(toast.success).mockReset()
  vi.mocked(toast.error).mockReset()
})

describe('OverviewPage', () => {
  it('walks an empty gateway through setup and creates a recommended profile', async () => {
    const created: Profile = { ...PROFILE_DEFAULTS, id: 'p1', revision: 1 }
    api.getOverview.mockResolvedValue(EMPTY_OVERVIEW)
    api.saveProfile.mockResolvedValue(created)
    renderOverview()

    expect(await screen.findByText('Get started')).toBeTruthy()
    expect(await screen.findByText('0 of 4 done')).toBeTruthy()
    expect(hrefOf('Add manually')).toBe('/admin/gateway/upstreams/new')
    expect(hrefsOf('Customize')).toContain('/admin/gateway/profiles/new')
    expect(
      screen.getByText('Available once the first two steps are done.'),
    ).toBeTruthy()
    expect(screen.getAllByText('—')).toHaveLength(2)
    expect(
      screen.getByText('No requests yet', { selector: 'p.font-medium' }),
    ).toBeTruthy()
    expect(screen.getByText('No degraded requests')).toBeTruthy()
    expect(
      screen.getByText(
        'Figures cover the latest 10,000 entries of the request log. Entries are kept for 30 days.',
      ),
    ).toBeTruthy()

    api.listProfiles.mockResolvedValue([created])
    fireEvent.click(screen.getByRole('button', { name: 'Use recommended' }))
    await waitFor(() => expect(api.saveProfile).toHaveBeenCalledTimes(1))
    const [connection, id, settings] = api.saveProfile.mock.calls[0]
    expect(connection.apiKey).toBe('admin-key')
    expect(id).toMatch(/^[0-9a-f]{16}$/)
    expect(settings).toEqual(PROFILE_DEFAULTS)
    expect(await screen.findByText('1 context profile ready')).toBeTruthy()
    expect(screen.getByText('1 of 4 done')).toBeTruthy()
    expect(toast.success).toHaveBeenCalledWith(
      'Created the context profile “Default”',
    )
  })

  it('reports a failed profile creation and keeps the step open', async () => {
    api.getOverview.mockResolvedValue(EMPTY_OVERVIEW)
    api.saveProfile.mockRejectedValue(
      new GatewayError(
        'Invalid gateway configuration; check field names, types and limits',
        422,
      ),
    )
    renderOverview()
    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Use recommended',
      }),
    )
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'The gateway rejected these settings. Check the values and try again.',
      ),
    )
    expect(screen.getByRole('button', { name: 'Use recommended' })).toBeTruthy()
  })

  it('summarizes traffic, OpenViking health and problems once requests arrive', async () => {
    api.getOverview.mockResolvedValue(BUSY_OVERVIEW)
    api.listLogs.mockResolvedValue(LOGS)
    renderOverview()

    expect(await screen.findByText('92%')).toBeTruthy()
    expect(screen.queryByText('Get started')).toBeNull()
    expect(api.listUpstreams).not.toHaveBeenCalled()
    expect(api.listLogs.mock.calls[0][1]).toBe(50)

    expect(screen.getByText('1,234')).toBeTruthy()
    expect(screen.getByText('1,234 new messages')).toBeTruthy()
    expect(screen.getByText('Last request 2 minutes ago')).toBeTruthy()
    expect(screen.getByText('56K output tokens')).toBeTruthy()
    expect(screen.getByText('50%')).toBeTruthy()
    expect(screen.getByText('3 tool steps')).toBeTruthy()
    expect(screen.getByText('Avg 820 ms over 40 searches')).toBeTruthy()

    expect(screen.getByText('Degraded')).toBeTruthy()
    expect(
      screen.getByText("OpenViking is unreachable or didn't answer in time"),
    ).toBeTruthy()
    expect(
      screen.getByText(
        '1 conversation is retrying saving to OpenViking after a failure.',
      ),
    ).toBeTruthy()
    // Saving problems and degraded requests both link to the issues filter.
    const issueLinks = screen
      .getAllByText('Show in Requests')
      .map((node) => node.closest('a')?.getAttribute('href'))
    expect(issueLinks).toEqual([
      '/admin/gateway/requests?filter=issues',
      '/admin/gateway/requests?filter=issues',
    ])
    // Each problem links the docs section that troubleshoots it.
    expect(hrefsOf('Troubleshooting')).toEqual([
      gatewayDocsUrl('openvikingDegraded', 'en'),
      gatewayDocsUrl('notSaved', 'en'),
      gatewayDocsUrl('degradedRequests', 'en'),
    ])
    expect(hrefOf('Docs')).toBe(gatewayDocsUrl('overview', 'en'))

    const reasons = screen
      .getAllByText(/^(Upstream switched|OpenViking plugin in use)$/, {
        selector: 'p',
      })
      .map((node) => node.textContent)
    expect(reasons).toEqual(['Upstream switched', 'OpenViking plugin in use'])

    expect(await screen.findByText('claude-sonnet-4-5')).toBeTruthy()
    // The memory column matches the request log's.
    expect(screen.getByText('+3')).toBeTruthy()
    expect(screen.getByTitle('3 memory entries added in 410 ms')).toBeTruthy()
    expect(
      screen.getByTitle(
        'Memory added to 4 earlier messages stayed in the history',
      ),
    ).toBeTruthy()
    // Memory sync events stay out of the recent model requests.
    expect(screen.queryByText('Memory sync')).toBeNull()
    expect(screen.queryByText('Paused')).toBeNull()
    expect(screen.getByText('502')).toBeTruthy()
    // No cache alerts, so no notice and no upstream list to load.
    expect(screen.queryByText(/prompt cache|cache hits lately/)).toBeNull()
    expect(hrefOf('View all')).toBe('/admin/gateway/requests')
    expect(
      screen.getByText(
        'Figures cover the latest 10,000 entries of the request log. Entries are kept for 7 days.',
      ),
    ).toBeTruthy()
  })

  it('warns about long conversations that keep missing the prompt cache', async () => {
    api.getOverview.mockResolvedValue({
      ...BUSY_OVERVIEW,
      cache_alerts: {
        misses: cacheMisses({
          conversations: 25,
          streak: 3,
          input_tokens: 33950,
          recent: [
            cacheMiss({ model: 'claude-sonnet-4-5', streak: 7 }),
            cacheMiss({ upstream_id: 'deadbeefcafe0001', streak: 5 }),
            cacheMiss({ model: 'kimi-k2', streak: 9, last_at: NOW - 7200 }),
          ],
        }),
        unreported: [],
      },
    })
    api.listUpstreams.mockResolvedValue(UPSTREAMS)
    renderOverview()

    expect(
      await screen.findByText(
        '25 long conversations keep missing the prompt cache',
      ),
    ).toBeTruthy()
    // The summary speaks for every conversation, listed or not, rounded down.
    expect(
      screen.getByText(
        /^Their last 3 model calls each sent at least 33K input tokens and got a 0% cache hit rate\./,
      ),
    ).toBeTruthy()
    expect(
      screen.getByText(/^Compare with your provider's cache records\./),
    ).toBeTruthy()
    // The newest conversations, upstreams by name.
    expect(screen.getByText('7 misses in a row')).toBeTruthy()
    expect(screen.getByText('5 misses in a row')).toBeTruthy()
    expect(screen.getByText('9 misses in a row')).toBeTruthy()
    expect(screen.getAllByText('claude-sonnet-4-5').length).toBeGreaterThan(0)
    expect(screen.getByText('2 hours ago')).toBeTruthy()
    expect(await screen.findAllByText('OpenAI production')).toHaveLength(2)
    expect(screen.getByText('deadbeef…').getAttribute('title')).toBe(
      'deadbeefcafe0001',
    )
    expect(screen.queryByText(/cache hits lately/)).toBeNull()
    expect(hrefsOf('Troubleshooting')).toContain(
      gatewayDocsUrl('cacheHitRate', 'en'),
    )
  })

  it('points Anthropic conversations that never write the cache at cache_control', async () => {
    api.getOverview.mockResolvedValue({
      ...BUSY_OVERVIEW,
      cache_alerts: {
        misses: cacheMisses({ streak: 3, no_writes: true }),
        unreported: [],
      },
    })
    api.listUpstreams.mockResolvedValue(UPSTREAMS)
    renderOverview()

    expect(
      await screen.findByText(
        '1 long conversation keeps missing the prompt cache',
      ),
    ).toBeTruthy()
    expect(
      screen.getByText(/^Its last 3 model calls each sent at least 50K/),
    ).toBeTruthy()
    expect(
      screen.getByText(/the client most likely doesn't set cache_control/),
    ).toBeTruthy()
    expect(screen.queryByText(/^Compare with your provider's/)).toBeNull()
  })

  it('lists every upstream that reports no cache hits in one note', async () => {
    api.getOverview.mockResolvedValue({
      ...BUSY_OVERVIEW,
      cache_alerts: {
        misses: null,
        unreported: ['deadbeefcafe0001', 'u1', 'u2'],
      },
    })
    api.listUpstreams.mockResolvedValue(UPSTREAMS)
    renderOverview()

    expect(
      await screen.findByText(
        "deadbeef…, OpenAI production, Kimi relay haven't reported any cache hits lately",
      ),
    ).toBeTruthy()
    expect(
      screen.getAllByText(/^Long conversations through them keep showing/),
    ).toHaveLength(1)
    expect(screen.queryByText(/prompt cache$/)).toBeNull()
  })

  it('shows the error with a retry when the overview fails', async () => {
    api.getOverview.mockRejectedValue(new GatewayError('Network Error'))
    renderOverview()
    expect(await screen.findByText("Couldn't load the overview")).toBeTruthy()
    expect(screen.getByText('Network Error')).toBeTruthy()
    api.getOverview.mockResolvedValue(BUSY_OVERVIEW)
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('92%')).toBeTruthy()
  })

  it('has every string translated in Chinese', async () => {
    await i18n.changeLanguage('zh-CN')
    api.getOverview.mockResolvedValue(EMPTY_OVERVIEW)
    const empty = renderOverview()
    expect(await screen.findByText('快速开始')).toBeTruthy()
    await screen.findByText('已完成 0/4')
    expect(empty.container.textContent).not.toMatch(
      /overview\.|enums\.|states\.|actions\./,
    )
    // The one-click profile gets a Chinese name.
    api.saveProfile.mockResolvedValue({
      ...PROFILE_DEFAULTS,
      name: '默认',
      id: 'p1',
      revision: 1,
    })
    fireEvent.click(screen.getByRole('button', { name: '使用推荐设置' }))
    await waitFor(() => expect(api.saveProfile).toHaveBeenCalledTimes(1))
    expect(api.saveProfile.mock.calls[0][2].name).toBe('默认')
    cleanup()

    api.getOverview.mockResolvedValue({
      ...BUSY_OVERVIEW,
      cache_alerts: {
        misses: cacheMisses({
          streak: 6,
          recent: [cacheMiss({ model: 'kimi-k2', streak: 6 })],
        }),
        unreported: ['u1'],
      },
    })
    api.listLogs.mockResolvedValue(LOGS)
    api.listUpstreams.mockResolvedValue(UPSTREAMS)
    const busy = renderOverview()
    expect(await screen.findByText('claude-sonnet-4-5')).toBeTruthy()
    expect(screen.getByText('1,234 条新消息')).toBeTruthy()
    expect(screen.getByText('有 1 段长对话连续没有命中提示词缓存')).toBeTruthy()
    expect(screen.getByText('连续 6 次未命中')).toBeTruthy()
    expect(
      await screen.findByText('上游 OpenAI production 最近没有返回过缓存命中'),
    ).toBeTruthy()
    // Chinese has no singular form, so the copy reads right for any count.
    expect(screen.getByText(/^相关长对话的缓存命中率一直是 0%/)).toBeTruthy()
    expect(busy.container.textContent).not.toMatch(
      /overview\.|enums\.|states\.|actions\./,
    )
    await i18n.changeLanguage('en')
  })
})

const ARK: Upstream = {
  ...UPSTREAM_DEFAULTS,
  id: 'vlm-ark',
  revision: 1,
  name: 'vlm-ark',
  protocol: 'chat',
  base_url: 'https://ark.example.com/api/v3',
  models: ['doubao-seed-1-6'],
  has_api_key: true,
}
const CODING_PLAN: Upstream = {
  ...ARK,
  id: 'vlm-kimi',
  name: 'vlm-kimi',
  coding_plan: true,
}
const DEFAULT_PROFILE: Profile = {
  ...PROFILE_DEFAULTS,
  id: 'default',
  revision: 1,
}
const ISSUED: IssuedKey = {
  id: 'k1',
  revision: 1,
  name: 'Quick mode',
  policy_id: 'default',
  upstream_ids: ['vlm-ark'],
  models: [],
  user_id: 'default',
  prefix: 'ovgw_quick12',
  created_at: NOW,
  key: 'ovgw_quickSecret',
}
const QUICK_REQUEST = {
  name: 'Quick mode',
  policy_id: 'default',
  upstream_ids: ['vlm-ark'],
  models: [],
}
const LOCKED: GatewayAccess = {
  managed: true,
  can_write_objects: false,
  can_import_vlm: false,
  can_issue_for_others: false,
  role: 'user',
  account_id: 'acme',
  user_id: 'alice',
  warnings: [],
}

const quickButton = () => screen.findByRole('button', { name: 'Get a key' })

describe('OverviewPage quick mode', () => {
  beforeEach(() => {
    api.getOverview.mockResolvedValue(EMPTY_OVERVIEW)
  })

  it('issues a key with the seeded upstream and profile in one request', async () => {
    host.serverMode = 'dev'
    api.listUpstreams.mockResolvedValue([ARK])
    api.listProfiles.mockResolvedValue([DEFAULT_PROFILE])
    api.issueKey.mockResolvedValue(ISSUED)
    renderOverview()

    expect(
      await screen.findByText(
        'Upstream: use vlm-ark (doubao-seed-1-6, Chat Completions)',
      ),
    ).toBeTruthy()
    expect(screen.getByText('Context profile: use “Default”')).toBeTruthy()
    expect(screen.getByText('Or set up step by step')).toBeTruthy()
    // The button takes the place of the heading; the region keeps the name.
    expect(
      within(screen.getByRole('region', { name: 'Quick mode' })).getByRole(
        'button',
        { name: 'Get a key' },
      ),
    ).toBeTruthy()
    expect(hrefsOf('Learn more')).toContain(gatewayDocsUrl('quickMode', 'en'))
    api.listKeys.mockResolvedValue([ISSUED])
    fireEvent.click(await quickButton())

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('ovgw_quickSecret')).toBeTruthy()
    expect(api.issueKey).toHaveBeenCalledTimes(1)
    expect(api.issueKey.mock.calls[0][1]).toEqual(QUICK_REQUEST)
    expect(api.importVlm).not.toHaveBeenCalled()
    expect(api.saveProfile).not.toHaveBeenCalled()
    // A chat-only key opens on the chat client, with the model filled in.
    const snippet = within(dialog).getAllByText(/v1\/chat\/completions/)[0]
    expect(snippet.textContent).toContain('doubao-seed-1-6')
    expect(snippet.textContent).toContain('http://gateway.test:1933')

    expect(await screen.findByText('3 of 4 done')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Get a key' })).toBeNull()
    expect(screen.getByRole('dialog')).toBe(dialog)
  })

  it('imports, creates the default profile and issues; a retry skips the import', async () => {
    let stored: Upstream[] = []
    api.listUpstreams.mockImplementation(async () => stored)
    api.importVlm.mockImplementation(async () => {
      stored = [ARK]
      return {
        imported: [{ id: 'vlm-ark', name: 'vlm-ark' }],
        needs_confirmation: [],
        skipped: [],
      }
    })
    api.saveProfile
      .mockRejectedValueOnce(new GatewayError('Invalid', 422))
      .mockResolvedValue(DEFAULT_PROFILE)
    api.issueKey.mockResolvedValue(ISSUED)
    renderOverview()

    expect(
      await screen.findByText(
        "Upstream: import the OpenViking server's model settings, reusing its API key and quota",
      ),
    ).toBeTruthy()
    expect(
      screen.getByText(
        'Context profile: create “Default” with the recommended settings',
      ),
    ).toBeTruthy()
    expect(screen.getByText('Key: issued for admin')).toBeTruthy()
    fireEvent.click(await quickButton())
    expect(
      await screen.findByText(
        'The gateway rejected these settings. Check the values and try again.',
      ),
    ).toBeTruthy()
    expect(api.issueKey).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('ovgw_quickSecret')).toBeTruthy()
    expect(api.importVlm).toHaveBeenCalledTimes(1)
    expect(api.saveProfile).toHaveBeenCalledTimes(2)
    const [, id, settings] = api.saveProfile.mock.calls[1]
    expect(id).toBe('default')
    expect(settings).toEqual(PROFILE_DEFAULTS)
    expect(api.issueKey.mock.calls[0][1]).toEqual(QUICK_REQUEST)
    const order = [api.importVlm, api.saveProfile, api.issueKey].map(
      (mock) => mock.mock.invocationCallOrder[0],
    )
    expect(order).toEqual([...order].sort((a, b) => a - b))
  })

  it('stops after an import that yields only an unconfirmed Coding Plan', async () => {
    let stored: Upstream[] = []
    api.listUpstreams.mockImplementation(async () => stored)
    api.importVlm.mockImplementation(async () => {
      stored = [CODING_PLAN]
      return {
        imported: [],
        needs_confirmation: [{ id: 'vlm-kimi', name: 'vlm-kimi' }],
        skipped: [],
      }
    })
    renderOverview()

    fireEvent.click(await quickButton())
    expect(
      await screen.findByText(/no model the gateway can relay directly/),
    ).toBeTruthy()
    const review = screen
      .getAllByText('Review')
      .map((node) => node.closest('a')?.getAttribute('href'))
    expect(review).toContain('/admin/gateway/upstreams/vlm-kimi')
    expect(api.saveProfile).not.toHaveBeenCalled()
    expect(api.issueKey).not.toHaveBeenCalled()
  })

  it('lets root pick the user in a prefilled issue dialog', async () => {
    api.getAccess.mockResolvedValue({
      managed: false,
      can_write_objects: true,
      can_import_vlm: true,
      can_issue_for_others: true,
      role: 'root',
      account_id: 'acme',
      user_id: 'admin',
      warnings: [],
    })
    api.listUpstreams.mockResolvedValue([ARK, CODING_PLAN])
    api.listProfiles.mockResolvedValue([DEFAULT_PROFILE])
    api.listKeyUsers.mockResolvedValue([
      { user_id: 'alice', role: 'user', api_key_available: true },
    ])
    renderOverview()

    expect(
      await screen.findByText(
        'Key: choose the OpenViking user who owns it in the next step',
      ),
    ).toBeTruthy()
    fireEvent.click(await quickButton())
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Issue a gateway key')).toBeTruthy()
    expect(within(dialog).getByLabelText('Name')).toBe(
      within(dialog).getByDisplayValue('Quick mode'),
    )
    expect(
      within(dialog).getByRole('combobox', { name: 'Context profile' })
        .textContent,
    ).toContain('Default')
    const checked = within(dialog)
      .getAllByRole('checkbox')
      .map((box) => box.getAttribute('aria-checked'))
    expect(checked).toEqual(['true', 'false'])
    expect(api.issueKey).not.toHaveBeenCalled()
  })

  it('issues while locked when an upstream and a profile exist', async () => {
    api.getAccess.mockResolvedValue(LOCKED)
    api.listUpstreams.mockResolvedValue([ARK])
    api.listProfiles.mockResolvedValue([DEFAULT_PROFILE])
    api.issueKey.mockResolvedValue({ ...ISSUED, user_id: 'alice' })
    renderOverview()

    fireEvent.click(await quickButton())
    await waitFor(() => expect(api.issueKey).toHaveBeenCalledTimes(1))
    expect(api.issueKey.mock.calls[0][1]).toEqual(QUICK_REQUEST)
  })

  it('offers unlocking while locked without a profile', async () => {
    api.getAccess.mockResolvedValue(LOCKED)
    api.listUpstreams.mockResolvedValue([ARK])
    renderOverview()

    const locked = await screen.findByText(
      'Creating a context profile or importing models needs the gateway management key.',
    )
    expect(
      within(locked.closest('section') as HTMLElement).getByRole('button'),
    ).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Get a key' })).toBeNull()
  })

  it("imports OpenViking's model settings from step 1 without issuing a key", async () => {
    let stored: Upstream[] = []
    api.listUpstreams.mockImplementation(async () => stored)
    api.importVlm.mockImplementation(async () => {
      stored = [ARK]
      return {
        imported: [{ id: 'vlm-ark', name: 'vlm-ark' }],
        needs_confirmation: [],
        skipped: [],
      }
    })
    renderOverview()

    const importButton = await screen.findByRole('button', {
      name: 'Import from OpenViking',
    })
    expect(hrefOf('Add manually')).toBe('/admin/gateway/upstreams/new')
    fireEvent.click(importButton)
    expect(
      await screen.findByText(
        "1 upstream, imported from OpenViking's model settings",
      ),
    ).toBeTruthy()
    expect(toast.success).toHaveBeenCalledWith(
      'Imported or updated 1 upstream.',
    )
    expect(screen.queryByText('Imported or updated 1 upstream.')).toBeNull()
    expect(api.importVlm).toHaveBeenCalledTimes(1)
    expect(api.saveProfile).not.toHaveBeenCalled()
    expect(api.issueKey).not.toHaveBeenCalled()
    expect(
      screen.queryByRole('button', { name: 'Import from OpenViking' }),
    ).toBeNull()
  })

  it('toasts a failed step-1 import and keeps the step open', async () => {
    api.importVlm.mockRejectedValue(
      new GatewayError('Unavailable', 409, undefined, 'vlm_import_unavailable'),
    )
    renderOverview()

    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Import from OpenViking',
      }),
    )
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "This gateway doesn't run inside OpenViking, so it can't read the server's model settings. Add upstreams manually.",
      ),
    )
    expect(
      screen.getByRole('button', { name: 'Import from OpenViking' }),
    ).toBeTruthy()
    expect(hrefOf('Add manually')).toBe('/admin/gateway/upstreams/new')
  })

  it('offers the step-1 import only when the gateway allows it', async () => {
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
    renderOverview()

    expect(await screen.findByText('0 of 4 done')).toBeTruthy()
    expect(hrefOf('Add upstream')).toBe('/admin/gateway/upstreams/new')
    expect(
      screen.queryByRole('button', { name: 'Import from OpenViking' }),
    ).toBeNull()
  })

  it('issues a key for the current user from step 3', async () => {
    host.serverMode = 'dev'
    api.listUpstreams.mockResolvedValue([ARK])
    api.listProfiles.mockResolvedValue([DEFAULT_PROFILE])
    api.issueKey.mockResolvedValue({ ...ISSUED, name: 'My key' })
    renderOverview()

    const step = (
      await screen.findByText('Clients call the gateway with this key.')
    ).closest('li') as HTMLElement
    api.listKeys.mockResolvedValue([ISSUED])
    fireEvent.click(within(step).getByRole('button', { name: 'Issue key' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('ovgw_quickSecret')).toBeTruthy()
    expect(api.issueKey.mock.calls[0][1]).toEqual({
      ...QUICK_REQUEST,
      name: 'My key',
    })
    expect(api.importVlm).not.toHaveBeenCalled()
    expect(api.saveProfile).not.toHaveBeenCalled()
  })

  it('keeps step 3 disabled until an upstream and a profile exist', async () => {
    renderOverview()

    const step = (
      await screen.findByText('Available once the first two steps are done.')
    ).closest('li') as HTMLElement
    expect(
      within(step).getByRole('button', { name: 'Issue key' }),
    ).toHaveProperty('disabled', true)
  })

  it('stays hidden on a standalone gateway that is locked without upstreams', async () => {
    host.gatewayHost = true
    api.getAccess.mockResolvedValue(LOCKED)
    renderOverview()

    expect(await screen.findByText('0 of 4 done')).toBeTruthy()
    expect(screen.queryByText('Quick mode')).toBeNull()
  })
})
