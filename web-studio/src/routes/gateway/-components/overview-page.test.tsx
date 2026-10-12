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

import { GatewayError } from '../-lib/api'
import type * as Api from '../-lib/api'
import type { LogRecord, Overview, Profile } from '../-lib/api'
import { PROFILE_DEFAULTS } from '../-lib/profile-schema'
import { OverviewPage } from './overview-page'

const api = vi.hoisted(() => ({
  getOverview: vi.fn(),
  listLogs: vi.fn(),
  listUpstreams: vi.fn(),
  listProfiles: vi.fn(),
  listKeys: vi.fn(),
  saveProfile: vi.fn(),
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
vi.mock('../-lib/api', async (importOriginal) => ({
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

/** Pages the overview links to; links only get an href for known routes. */
const LINK_TARGETS = [
  '/gateway/upstreams',
  '/gateway/upstreams/$upstreamId',
  '/gateway/profiles',
  '/gateway/profiles/$profileId',
  '/gateway/keys',
  '/gateway/requests',
  '/gateway/connect',
]

/** Href of a button-styled link (Base UI gives those the button role). */
function hrefOf(text: string) {
  return screen.getByText(text).closest('a')?.getAttribute('href')
}

function renderOverview() {
  const root = createRootRoute({ component: Outlet })
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({
        getParentRoute: () => root,
        path: '/gateway',
        component: OverviewPage,
      }),
      ...LINK_TARGETS.map((path) =>
        createRoute({ getParentRoute: () => root, path }),
      ),
    ]),
    history: createMemoryHistory({ initialEntries: ['/gateway'] }),
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
    expect(hrefOf('Add upstream')).toBe('/gateway/upstreams/new')
    expect(hrefOf('Customize')).toBe('/gateway/profiles/new')
    expect(
      screen.getByText('Add an upstream and a context profile first.'),
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
    fireEvent.click(
      screen.getByRole('button', { name: 'Create with recommended settings' }),
    )
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
        name: 'Create with recommended settings',
      }),
    )
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'The gateway rejected these settings. Check the values and limits.',
      ),
    )
    expect(
      screen.getByRole('button', { name: 'Create with recommended settings' }),
    ).toBeTruthy()
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
      '/gateway/requests?filter=issues',
      '/gateway/requests?filter=issues',
    ])

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
    expect(hrefOf('View all')).toBe('/gateway/requests')
    expect(
      screen.getByText(
        'Figures cover the latest 10,000 entries of the request log. Entries are kept for 7 days.',
      ),
    ).toBeTruthy()
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
    fireEvent.click(screen.getByRole('button', { name: '使用推荐设置创建' }))
    await waitFor(() => expect(api.saveProfile).toHaveBeenCalledTimes(1))
    expect(api.saveProfile.mock.calls[0][2].name).toBe('默认')
    cleanup()

    api.getOverview.mockResolvedValue(BUSY_OVERVIEW)
    api.listLogs.mockResolvedValue(LOGS)
    const busy = renderOverview()
    expect(await screen.findByText('claude-sonnet-4-5')).toBeTruthy()
    expect(screen.getByText('1,234 条新消息')).toBeTruthy()
    expect(busy.container.textContent).not.toMatch(
      /overview\.|enums\.|states\.|actions\./,
    )
    await i18n.changeLanguage('en')
  })
})
