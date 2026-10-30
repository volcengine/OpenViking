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
  fireEvent,
  render,
  screen,
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

import { CLIENT_STEPS } from '#/features/admin/gateway/components/connect-client-guide'
import { ConnectPage } from '#/features/admin/gateway/components/connect-page'
import { CodeText } from '#/features/admin/gateway/components/connect-text'
import type * as Api from '#/features/admin/gateway/lib/api'
import type { ConnectionInfo, Upstream } from '#/features/admin/gateway/lib/api'
import {
  CLIENT_IDS,
  clientSnippets,
  gatewayDocsUrl,
} from '#/features/admin/gateway/lib/client-guides'
import { parseConnectSearch } from '#/features/admin/gateway/lib/search'

const api = vi.hoisted(() => ({
  getConnectionInfo: vi.fn(),
  listUpstreams: vi.fn(),
}))

const host = vi.hoisted(() => ({ isGatewayHost: false }))

vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({ ready: true, provider: 'opensource' }),
}))

vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    isGatewayHost: host.isGatewayHost,
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
vi.mock('#/features/admin/gateway/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof Api>()),
  getConnectionInfo: api.getConnectionInfo,
  listUpstreams: api.listUpstreams,
}))

const PUBLIC: ConnectionInfo = {
  base_url: 'https://gw.example.com/gateway',
  public_url_configured: true,
}

function upstream(overrides: Partial<Upstream>): Upstream {
  return {
    id: 'u1',
    revision: 1,
    name: 'Upstream',
    protocol: 'chat',
    base_url: 'https://api.example.com/v1',
    append_endpoint_path: true,
    auth_mode: 'managed',
    key_header: 'authorization',
    models: [],
    aliases: {},
    priority: 0,
    enabled: true,
    vendor: 'generic',
    allow_gateway_tools: true,
    replay_reasoning: null,
    coding_plan: false,
    allow_coding_plan: false,
    cache_min_tokens: 1024,
    context_windows: {},
    has_api_key: true,
    header_names: [],
    ...overrides,
  }
}

const UPSTREAMS = [
  upstream({ id: 'chat', name: 'OpenAI chat' }),
  upstream({
    id: 'responses',
    name: 'Team Responses',
    protocol: 'responses',
    auth_mode: 'passthrough',
  }),
  upstream({
    id: 'claude',
    name: 'Claude backup',
    protocol: 'anthropic',
    enabled: false,
  }),
]

function renderPage(path = '/admin/gateway/connect') {
  const root = createRootRoute({ component: Outlet })
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({
        getParentRoute: () => root,
        path: '/admin/gateway/connect',
        validateSearch: parseConnectSearch,
        component: ConnectPage,
      }),
      createRoute({
        getParentRoute: () => root,
        path: '/admin/gateway/keys',
      }),
      createRoute({
        getParentRoute: () => root,
        path: '/admin/gateway/upstreams/$upstreamId',
      }),
    ]),
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

function snippetTexts(): string[] {
  return Array.from(
    document.querySelectorAll('pre code'),
    (node) => node.textContent,
  )
}

/** Href of a button that renders a link (Base UI gives it the button role). */
function linkTo(text: string) {
  return screen.getByText(text).closest('a')?.getAttribute('href')
}

function clientNav() {
  return within(screen.getByRole('navigation', { name: 'Clients' }))
}

beforeAll(async () => {
  await i18n.changeLanguage('en')
})

it('gives every snippet of every client a step', () => {
  for (const client of CLIENT_IDS) {
    const ids = clientSnippets(client, { baseUrl: PUBLIC.base_url }).map(
      (snippet) => snippet.id,
    )
    expect(CLIENT_STEPS[client], client).toEqual(expect.arrayContaining(ids))
  }
})

it('renders backtick spans as inline code', () => {
  render(<CodeText text="Set `public_url` in `ov.conf`." />)
  expect(screen.getByText('public_url').tagName).toBe('CODE')
  expect(screen.getByText('ov.conf').tagName).toBe('CODE')
  cleanup()
})

describe('ConnectPage', () => {
  beforeEach(() => {
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    api.getConnectionInfo.mockReset().mockResolvedValue(PUBLIC)
    api.listUpstreams.mockReset().mockResolvedValue(UPSTREAMS)
  })
  afterEach(cleanup)

  it('shows the address for both client families and links keys and docs', async () => {
    renderPage()
    expect(
      await screen.findByText('https://gw.example.com/gateway'),
    ).toBeTruthy()
    expect(screen.getByText('https://gw.example.com/gateway/v1')).toBeTruthy()
    expect(screen.queryByText(/public_url/)).toBeNull()
    expect(linkTo('Manage keys')).toBe('/admin/gateway/keys')
    expect(linkTo('Full guide')).toBe(gatewayDocsUrl('claudeCode', 'en'))
    // Each reference card links its docs section.
    expect(
      screen
        .getAllByText('Learn more')
        .map((node) => node.closest('a')?.getAttribute('href')),
    ).toEqual([
      gatewayDocsUrl('conversations', 'en'),
      gatewayDocsUrl('providerKey', 'en'),
      gatewayDocsUrl('contextHeader', 'en'),
      gatewayDocsUrl('pluginDetected', 'en'),
    ])
  })

  it('tells plugins apart from OpenViking MCP tools', async () => {
    renderPage()
    expect(
      await screen.findByText(
        'The gateway recognizes a plugin by the memory blocks it adds to messages. Integrations can also announce themselves with this header.',
      ),
    ).toBeTruthy()
    expect(
      screen.getByText(/^OpenViking MCP tools alone don't count/),
    ).toBeTruthy()
  })

  it.each([
    [
      'a loopback address',
      {
        base_url: 'http://127.0.0.1:1933/gateway',
        public_url_configured: false,
      },
      "Only the gateway's own machine can use this address",
    ],
    [
      'an address that is not the public one',
      { base_url: 'http://gateway:1933/gateway', public_url_configured: false },
      'Clients may not be able to reach this address',
    ],
  ])('warns about %s', async (_, info, title) => {
    api.getConnectionInfo.mockResolvedValue(info)
    renderPage()
    expect(await screen.findByText(title)).toBeTruthy()
    expect(screen.getAllByText('gateway.public_url')).toHaveLength(1)
    const docs = within(screen.getByText(title).parentElement as HTMLElement)
    expect(
      docs.getByText('Learn more').closest('a')?.getAttribute('href'),
    ).toBe(gatewayDocsUrl('publicAddress', 'en'))
  })

  it('points a standalone gateway at gateway.conf instead of ov.conf', async () => {
    host.isGatewayHost = true
    api.getConnectionInfo.mockResolvedValue({
      base_url: 'http://gateway:1935/gateway',
      public_url_configured: false,
    })
    try {
      renderPage()
      expect(
        await screen.findByText(
          'Clients may not be able to reach this address',
        ),
      ).toBeTruthy()
      expect(screen.getByText('gateway.conf')).toBeTruthy()
      expect(screen.getByText('OPENVIKING_GATEWAY_PUBLIC_URL')).toBeTruthy()
      expect(screen.queryByText('ov.conf')).toBeNull()
    } finally {
      host.isGatewayHost = false
    }
  })

  it('starts with Claude Code and warns when no enabled upstream speaks its protocol', async () => {
    renderPage()
    expect(
      await screen.findByRole('heading', { name: 'Claude Code' }),
    ).toBeTruthy()
    expect(snippetTexts()).toContain(
      [
        'export ANTHROPIC_BASE_URL=https://gw.example.com/gateway',
        "export ANTHROPIC_AUTH_TOKEN='<gateway-key>'",
        'export CLAUDE_CODE_GATEWAY_HINT_HEADERS=1',
      ].join('\n'),
    )
    expect(
      await screen.findByText('Add an upstream for Anthropic Messages first'),
    ).toBeTruthy()
    expect(linkTo('Add upstream')).toBe('/admin/gateway/upstreams/new')
    expect(
      clientNav()
        .getByRole('link', { name: /Claude Code/ })
        .getAttribute('aria-current'),
    ).toBe('page')
    expect(
      clientNav().getByRole('link', { name: /Claude Code/ }).textContent,
    ).toContain('No enabled upstream for this client yet')
  })

  it('opens the client named in the URL', async () => {
    renderPage('/admin/gateway/connect?client=codex')
    expect(
      await screen.findByRole('heading', { name: 'Codex CLI' }),
    ).toBeTruthy()
    expect(
      snippetTexts().some((code) =>
        code.includes('base_url = "https://gw.example.com/gateway/v1"'),
      ),
    ).toBe(true)
    expect(
      await screen.findByText('Available upstreams: Team Responses'),
    ).toBeTruthy()
    expect(screen.queryByText(/^Add an upstream for/)).toBeNull()
    expect(screen.getByText('<model>')).toBeTruthy()
    // The full guide opens the picked client's section.
    expect(linkTo('Full guide')).toBe(gatewayDocsUrl('codex', 'en'))
  })

  it('falls back to Claude Code for an unknown client', async () => {
    renderPage('/admin/gateway/connect?client=cursor')
    expect(
      await screen.findByRole('heading', { name: 'Claude Code' }),
    ).toBeTruthy()
  })

  it('keeps the picked client in the URL', async () => {
    const router = renderPage()
    await screen.findByRole('heading', { name: 'Claude Code' })
    fireEvent.click(clientNav().getByRole('link', { name: 'Open WebUI' }))
    expect(
      await screen.findByRole('heading', { name: 'Open WebUI' }),
    ).toBeTruthy()
    expect(router.state.location.search).toEqual({ client: 'open-webui' })
    expect(snippetTexts().some((code) => code.includes('{{CHAT_ID}}'))).toBe(
      true,
    )
  })

  it('switches the snippet and upstream check with the picked protocol', async () => {
    const router = renderPage(
      '/admin/gateway/connect?client=pi&protocol=anthropic',
    )
    expect(await screen.findByRole('heading', { name: 'pi' })).toBeTruthy()
    expect(
      await screen.findByText('Add an upstream for Anthropic Messages first'),
    ).toBeTruthy()
    expect(
      snippetTexts().some((code) => code.includes('"anthropic-messages"')),
    ).toBe(true)

    const picker = within(
      screen.getByRole('group', { name: 'Calls the gateway with' }),
    )
    fireEvent.click(picker.getByRole('link', { name: 'Responses' }))
    expect(
      await screen.findByText('Available upstreams: Team Responses'),
    ).toBeTruthy()
    expect(router.state.location.search).toEqual({
      client: 'pi',
      protocol: 'responses',
    })
    expect(
      snippetTexts().some((code) => code.includes('"openai-responses"')),
    ).toBe(true)
    expect(screen.queryByText(/^Add an upstream for/)).toBeNull()
  })

  it('starts a per-protocol client on a protocol an upstream serves', async () => {
    api.listUpstreams.mockResolvedValue([UPSTREAMS[1]])
    renderPage('/admin/gateway/connect?client=dsh')
    expect(
      await screen.findByText('Available upstreams: Team Responses'),
    ).toBeTruthy()
    expect(
      snippetTexts().some((code) => code.includes('api: openai-responses')),
    ).toBe(true)
  })

  it('lists the upstreams that need the client’s own provider key', async () => {
    renderPage()
    expect(
      await screen.findByText('Upstreams that need it: Team Responses'),
    ).toBeTruthy()
  })

  it('has copy for every client', async () => {
    renderPage()
    await screen.findByRole('heading', { name: 'Claude Code' })
    for (const client of CLIENT_IDS) {
      fireEvent.click(
        clientNav().getAllByRole('link')[CLIENT_IDS.indexOf(client)],
      )
      await screen.findByRole('heading', {
        name: i18n.t(`gateway:connect.clients.${client}.name`),
      })
      expect(document.body.textContent, client).not.toMatch(
        /\b(connect|enums|states)\.[\w.-]+/,
      )
    }
  })
})
