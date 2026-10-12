// @vitest-environment jsdom
import { createHash } from 'node:crypto'
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
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest'

import i18n from '#/i18n'

import type * as Api from '../-lib/api'
import type { GatewayKey, LogRecord, Upstream } from '../-lib/api'
import { parseRequestsSearch } from '../-lib/search'
import { RequestsPage } from './requests-page'

const api = vi.hoisted(() => ({
  listLogs: vi.fn(),
  listUpstreams: vi.fn(),
  listKeys: vi.fn(),
  resyncCapture: vi.fn(),
}))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))

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
vi.mock('sonner', () => ({ toast }))

const NOW = Math.floor(Date.now() / 1000)
const SESSION = 'a'.repeat(64)

const UPSTREAMS = [{ id: 'u1', name: 'OpenAI production' }] as Upstream[]
const KEYS = [
  { id: 'k1', name: 'Laptop', prefix: 'ovgw_Ab3dE9x' },
] as GatewayKey[]

const RECORDS: LogRecord[] = [
  {
    time: NOW - 30,
    request_id: 'req-user',
    kind: 'user',
    model: 'gpt-5',
    status: 200,
    upstream_id: 'u1',
    session: SESSION,
    protocol: 'chat',
    credential_id: 'k1',
    input_tokens: 12000,
    cached_tokens: 9000,
    output_tokens: 300,
    recall_count: 3,
    recall_ms: 420,
    recall_reason: 'recalled',
    replay_hits: 2,
    capture_status: 'active',
  },
  {
    time: NOW - 60,
    request_id: 'req-tool',
    kind: 'continuation',
    model: 'gpt-5-mini',
    status: 200,
    upstream_id: 'u1',
    session: SESSION,
    protocol: 'chat',
    credential_id: 'k1',
    capture_status: 'active',
  },
  {
    time: NOW - 90,
    request_id: 'req-switch',
    kind: 'user',
    model: 'deepseek-chat',
    status: 200,
    upstream_id: 'removed-upstream',
    degradation: 'upstream_changed',
    session: SESSION,
    protocol: 'chat',
    credential_id: 'k1',
    capture_status: 'active',
  },
  {
    time: NOW - 120,
    kind: 'capture',
    session: 'b'.repeat(64),
    protocol: 'anthropic',
    credential_id: 'revoked-key',
    capture_status: 'paused',
    capture_reason: 'openviking_http_401',
  },
  {
    time: NOW - 150,
    request_id: 'req-error',
    kind: 'passthrough',
    model: 'claude-sonnet',
    status: 502,
  },
]

function renderPage(path = '/gateway/requests') {
  const root = createRootRoute({ component: Outlet })
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({
        getParentRoute: () => root,
        path: '/gateway/requests',
        validateSearch: parseRequestsSearch,
        component: RequestsPage,
      }),
      createRoute({
        getParentRoute: () => root,
        path: '/gateway/connect',
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

/** The table row that shows `text`. */
function rowOf(text: string) {
  const row = screen.getByText(text).closest('tr')
  if (!row) throw new Error(`No row for ${text}`)
  return row
}

function expand(text: string) {
  fireEvent.click(
    within(rowOf(text)).getByRole('button', { name: 'Show details' }),
  )
}

beforeAll(async () => {
  await i18n.changeLanguage('en')
  // jsdom applies no CSS, so queries would also find the copies that tables
  // show only below `md`; leave those out and test the desktop layout.
  configure({ defaultIgnore: 'script, style, .md\\:hidden, .md\\:hidden *' })
})

beforeEach(() => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  api.listLogs.mockReset().mockResolvedValue(RECORDS)
  api.listUpstreams.mockReset().mockResolvedValue(UPSTREAMS)
  api.listKeys.mockReset().mockResolvedValue(KEYS)
  api.resyncCapture.mockReset().mockResolvedValue({ status: 'ok' })
  toast.success.mockReset()
  toast.error.mockReset()
})
afterEach(cleanup)

describe('RequestsPage', () => {
  it('loads the latest 1,000 records and summarizes each request', async () => {
    renderPage()
    expect(await screen.findByText('gpt-5')).toBeTruthy()
    expect(api.listLogs.mock.calls[0][1]).toBe(1000)

    const row = rowOf('gpt-5')
    expect(within(row).getByText('New message')).toBeTruthy()
    expect(within(row).getByText('12K in · 75% cached · 300 out')).toBeTruthy()
    expect(within(row).getByText('+3')).toBeTruthy()
    expect(
      within(row).getByText('3 memory entries added in 420 ms'),
    ).toBeTruthy()
    expect(
      within(row).getByText(
        'Memory added to 2 earlier messages stayed in the history',
      ),
    ).toBeTruthy()
    expect(
      within(rowOf('deepseek-chat')).getByText('Upstream switched'),
    ).toBeTruthy()
    expect(within(rowOf('claude-sonnet')).getByText('502')).toBeTruthy()

    const issues = screen.getByRole('button', { name: /Issues/ })
    expect(within(issues).getByText('3')).toBeTruthy()
    expect(
      screen.getByText('Filters and search cover the latest 1,000 records.'),
    ).toBeTruthy()
  })

  it('shows a dash for requests that report no token usage', async () => {
    api.listLogs.mockResolvedValue([
      {
        time: NOW - 30,
        request_id: 'req-count',
        kind: 'count',
        model: 'claude-count',
        status: 200,
        input_tokens: 0,
        output_tokens: 0,
      },
    ])
    renderPage()
    await screen.findByText('claude-count')
    const row = rowOf('claude-count')
    expect(within(row).queryByText('0 in · 0 out')).toBeNull()
    // Tokens, memory and saving are all empty.
    expect(within(row).getAllByText('—')).toHaveLength(3)
  })

  it('opens the issues view from the URL and switches views', async () => {
    const router = renderPage('/gateway/requests?filter=issues')
    expect(await screen.findByText('deepseek-chat')).toBeTruthy()
    expect(screen.getByText('claude-sonnet')).toBeTruthy()
    expect(screen.getByText('Paused')).toBeTruthy()
    expect(screen.queryByText('gpt-5')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /New messages/ }))
    await waitFor(() => expect(screen.getByText('gpt-5')).toBeTruthy())
    expect(screen.getByText('deepseek-chat')).toBeTruthy()
    expect(screen.queryByText('gpt-5-mini')).toBeNull()
    expect(screen.queryByText('claude-sonnet')).toBeNull()
    expect(router.state.location.search).toEqual({ filter: 'messages' })

    fireEvent.click(screen.getByRole('button', { name: /Tool steps/ }))
    await waitFor(() => expect(screen.getByText('gpt-5-mini')).toBeTruthy())
    expect(screen.queryByText('gpt-5')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /^All/ }))
    await waitFor(() => expect(router.state.location.search).toEqual({}))
    expect(screen.getByText('claude-sonnet')).toBeTruthy()
  })

  it('filters by request type and leaves a conflicting view', async () => {
    const router = renderPage('/gateway/requests?filter=messages')
    await screen.findByText('gpt-5')
    fireEvent.click(screen.getByRole('combobox', { name: 'Request type' }))
    const option = await screen.findByRole('option', { name: 'Memory sync' })
    // Base UI selects only the highlighted option; hovering highlights it.
    fireEvent.mouseMove(option)
    fireEvent.click(option)

    await waitFor(() => expect(router.state.location.search).toEqual({}))
    expect(screen.queryByText('gpt-5')).toBeNull()
    expect(within(rowOf('Paused')).getByText('Memory sync')).toBeTruthy()
  })

  it('searches by key name and offers to clear filters', async () => {
    renderPage()
    await screen.findByText('gpt-5')
    const search = screen.getByRole('searchbox', {
      name: 'Search model, conversation or key',
    })

    fireEvent.change(search, { target: { value: 'laptop' } })
    await waitFor(() => expect(screen.queryByText('claude-sonnet')).toBeNull())
    expect(screen.getByText('gpt-5')).toBeTruthy()
    expect(screen.getByText('deepseek-chat')).toBeTruthy()

    fireEvent.change(search, { target: { value: 'no-such-model' } })
    expect(await screen.findByText('No matching requests')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(await screen.findByText('claude-sonnet')).toBeTruthy()
  })

  it('finds a conversation by the id the client sent', async () => {
    const clientId = 'open-webui-chat-42'
    api.listLogs.mockResolvedValue([
      ...RECORDS,
      {
        time: NOW - 200,
        request_id: 'req-webui',
        kind: 'user',
        model: 'webui-model',
        session: createHash('sha256').update(clientId).digest('hex'),
      },
    ])
    renderPage()
    await screen.findByText('webui-model')
    fireEvent.change(
      screen.getByRole('searchbox', {
        name: 'Search model, conversation or key',
      }),
      { target: { value: clientId } },
    )
    await waitFor(() => expect(screen.queryByText('gpt-5')).toBeNull())
    expect(screen.getByText('webui-model')).toBeTruthy()
  })

  it('explains an issue and names the upstream and key in the details', async () => {
    renderPage()
    await screen.findByText('deepseek-chat')
    expand('deepseek-chat')
    expect(screen.getByText('Deleted upstream')).toBeTruthy()
    expect(screen.getByText('Laptop')).toBeTruthy()
    expect(screen.getByText(/is no longer usable/)).toBeTruthy()
    expect(screen.getByText(/Re-enable the original upstream/)).toBeTruthy()

    expand('claude-sonnet')
    expect(
      screen.getByText(
        'The upstream answered with HTTP 502, and the client received this error.',
      ),
    ).toBeTruthy()
  })

  it('shows how full the context window was and what replaced earlier history', async () => {
    api.listLogs.mockResolvedValue([
      {
        time: NOW - 30,
        request_id: 'req-compacted',
        kind: 'user',
        model: 'compacted-model',
        status: 200,
        context_tokens: 180000,
        context_window: 200000,
        compaction_applied: 'anchor-1',
        compaction_tokens: 2400,
        compaction_ms: 8500,
      },
      {
        time: NOW - 60,
        request_id: 'req-window',
        kind: 'continuation',
        model: 'window-model',
        status: 200,
        context_tokens: 150000,
        context_window: 200000,
        compaction_failed: 'summary_truncated',
        window: 2,
        window_reset: true,
        window_reminder: 'hard',
      },
    ])
    renderPage()
    await screen.findByText('compacted-model')
    expand('compacted-model')
    expect(
      screen.getByText('About 180,000 of 200,000 tokens (90%)'),
    ).toBeTruthy()
    expect(
      screen.getByText('Sent with the earlier history replaced'),
    ).toBeTruthy()
    expect(
      screen.getByText('Summary of about 2,400 tokens written in 8.5 sec'),
    ).toBeTruthy()

    expand('window-model')
    expect(
      screen.getByText(
        'Compaction failed (summary_truncated), so the full history was sent',
      ),
    ).toBeTruthy()
    expect(screen.getByText('Window 2, managed by the model')).toBeTruthy()
    expect(screen.getByText('The model started a new window')).toBeTruthy()
    expect(
      screen.getByText('Told the model to start a new window now'),
    ).toBeTruthy()
  })

  it('resyncs a conversation after confirmation', async () => {
    renderPage()
    await screen.findByText('gpt-5')
    expand('gpt-5')
    expect(screen.getByText('OpenAI production')).toBeTruthy()

    fireEvent.click(
      screen.getByRole('button', { name: 'Resync conversation…' }),
    )
    expect(
      await screen.findByText(
        'Start a fresh OpenViking session for this conversation?',
      ),
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Resync' }))

    await waitFor(() => expect(toast.success).toHaveBeenCalled())
    expect(api.resyncCapture).toHaveBeenCalledWith(
      expect.objectContaining({ apiKey: 'admin-key' }),
      'k1',
      { session: SESSION, protocol: 'chat' },
    )
    expect(toast.success).toHaveBeenCalledWith(
      'The conversation will be saved again with its next message.',
    )
  })

  it('does not offer a resync once the key is revoked', async () => {
    renderPage()
    await screen.findByText('Memory sync')
    expand('Memory sync')
    expect(screen.getByText('Revoked key')).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Resync conversation…' }),
    ).toHaveProperty('disabled', true)
    expect(
      screen.getByText(
        "The gateway key this conversation used has been revoked, so it can't be resynced.",
      ),
    ).toBeTruthy()
  })

  it('pages through 25 records at a time', async () => {
    api.listLogs.mockResolvedValue(
      Array.from({ length: 30 }, (_, index) => ({
        time: NOW - index,
        request_id: `req-${index}`,
        kind: 'user',
        model: `model-${index}`,
        status: 200,
      })),
    )
    renderPage()
    expect(await screen.findByText('model-0')).toBeTruthy()
    expect(screen.getByText('model-24')).toBeTruthy()
    expect(screen.queryByText('model-25')).toBeNull()
    expect(screen.getByText('30 records · page 1 of 2')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '2' }))
    expect(await screen.findByText('model-29')).toBeTruthy()
    expect(screen.queryByText('model-0')).toBeNull()
  })

  it('points to client setup when nothing has been logged yet', async () => {
    api.listLogs.mockResolvedValue([])
    renderPage()
    expect(await screen.findByText('No requests yet')).toBeTruthy()
    const connect = screen.getByRole('button', { name: 'Connect a client' })
    expect(connect.closest('a')?.getAttribute('href')).toBe('/gateway/connect')
  })
})
