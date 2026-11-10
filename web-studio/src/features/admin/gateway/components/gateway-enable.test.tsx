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
  waitFor,
} from '@testing-library/react'
import type * as ReactI18next from 'react-i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { GatewayLayout } from '#/features/admin/gateway/components/gateway-layout'
import type * as Api from '#/features/admin/gateway/lib/api'
import type { GatewayConnection } from '#/features/admin/gateway/lib/api'
import { GatewayError } from '#/features/admin/gateway/lib/api'
import { gatewayDocsUrl } from '#/features/admin/gateway/lib/client-guides'
import { getManagementKey } from '#/features/admin/gateway/lib/management-key'
import type * as ConfigFileApi from '#/features/admin/server-configuration/lib/config-file-api'

const BASE_URL = 'http://ov.example.com'

const state = vi.hoisted(() => ({
  role: 'root',
  serverMode: 'api_key',
  gatewayOn: false,
  get: vi.fn(),
  save: vi.fn(),
  restart: vi.fn(),
  copy: vi.fn(),
  getAccess: vi.fn(),
  getConnectionInfo: vi.fn(),
}))
vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({ ready: true, provider: 'opensource' }),
}))

vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'http://ov.example.com',
      accountId: 'acme',
      userId: 'root',
      apiKey: '',
      adminApiKey: 'root-key',
    },
    connectionRole: state.role,
    isConnectionRoleLoading: false,
    serverMode: state.serverMode,
    identityScopeKey: 'scope',
    isGatewayHost: false,
    openvikingUrl: '',
  }),
}))
vi.mock(
  '#/features/admin/server-configuration/lib/config-file-api',
  async (original) => ({
    ...(await original<typeof ConfigFileApi>()),
    createConfigFileApi: () => ({
      get: state.get,
      save: state.save,
      restart: state.restart,
    }),
  }),
)
vi.mock('#/features/admin/gateway/lib/api', async (original) => ({
  ...(await original<typeof Api>()),
  getAccess: state.getAccess,
  getConnectionInfo: state.getConnectionInfo,
}))
vi.mock('#/shared/lib/clipboard', () => ({ copyTextToClipboard: state.copy }))
vi.mock('react-i18next', async (original) => ({
  ...(await original<typeof ReactI18next>()),
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: 'en' },
  }),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const CONTENT =
  '{\n  "server": {"port": 1933},\n  "gateway": {"storage_path": "/data"}\n}\n'

function file(overrides: Record<string, unknown> = {}) {
  return {
    content: CONTENT,
    revision: 'r1',
    writable: true,
    models: { vlm: { config: {} }, embedding: { config: {} } },
    restart: { supported: true, instance_id: 'old', restarting: false },
    ...overrides,
  }
}

const restarted = (extra: Record<string, unknown> = {}) =>
  file({
    revision: 'r2',
    restart: {
      supported: true,
      instance_id: 'new',
      restarting: false,
      ...extra,
    },
  })

/** Renders the card the way users see it: inside the gateway layout. */
function renderGateway() {
  const root = createRootRoute({ component: Outlet })
  const router = createRouter({
    routeTree: root.addChildren([
      createRoute({
        getParentRoute: () => root,
        path: '/admin/gateway',
        component: GatewayLayout,
      }),
      createRoute({ getParentRoute: () => root, path: '/settings' }),
    ]),
    history: createMemoryHistory({ initialEntries: ['/admin/gateway'] }),
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

async function confirmEnable({ protect = false } = {}) {
  fireEvent.click(
    await screen.findByRole('button', { name: 'unavailable.enable.action' }),
  )
  if (protect) fireEvent.click(screen.getByRole('checkbox'))
  const buttons = screen.getAllByRole('button', {
    name: 'unavailable.enable.action',
  })
  fireEvent.click(buttons[buttons.length - 1])
}

/** Whether a gateway call carried this management key. */
const accessedWith = (key: string) =>
  state.getAccess.mock.calls.some(
    (call) => (call[0] as GatewayConnection).managementKey === key,
  )

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  sessionStorage.clear()
  state.role = 'root'
  state.serverMode = 'api_key'
  state.gatewayOn = false
  state.get.mockResolvedValue(file())
  state.save.mockResolvedValue({ revision: 'r2', restart_required: true })
  state.restart.mockResolvedValue({
    supported: true,
    instance_id: 'old',
    restarting: true,
  })
  state.getAccess.mockImplementation(async () => {
    if (!state.gatewayOn)
      throw new GatewayError('Not Found', 404, undefined, 'not_enabled')
    return {
      managed: true,
      can_write_objects: true,
      can_import_vlm: true,
      can_issue_for_others: true,
      role: 'root',
      account_id: 'acme',
      user_id: 'root',
      warnings: [],
    }
  })
  state.getConnectionInfo.mockResolvedValue({
    base_url: 'https://gateway.example.com',
    public_url_configured: true,
  })
})
afterEach(cleanup)

describe('enabling the gateway from Studio', () => {
  it('writes ov.conf, restarts, shows the new management key once and then opens the gateway with it', async () => {
    state.get.mockResolvedValueOnce(file()).mockResolvedValue(restarted())
    state.restart.mockImplementation(async () => {
      state.gatewayOn = true
      return { supported: true, instance_id: 'old', restarting: true }
    })
    state.copy.mockRejectedValue(new Error('denied'))
    renderGateway()
    expect(
      await screen.findByText('unavailable.enable.description'),
    ).toBeTruthy()
    await confirmEnable({ protect: true })

    const key = await screen.findByLabelText<HTMLInputElement>(
      'unavailable.enable.keyTitle',
    )
    expect(state.restart).toHaveBeenCalledWith('r2')
    const [content, revision] = state.save.mock.calls[0]
    expect(revision).toBe('r1')
    const written = JSON.parse(content)
    expect(written.server).toEqual({ port: 1933 })
    expect(written.gateway).toMatchObject({
      storage_path: '/data',
      enabled: true,
    })
    expect(written.gateway.management_key).toMatch(/^ovgm_[A-Za-z0-9_-]{43}$/)
    expect(key.value).toBe(written.gateway.management_key)
    expect(screen.getByText('unavailable.enable.keyOnce')).toBeTruthy()
    // Storing the key would switch the gateway queries and drop this card.
    expect(getManagementKey(BASE_URL)).toBe('')

    fireEvent.click(
      screen.getByRole('button', { name: 'unavailable.enable.copyKey' }),
    )
    expect(
      await screen.findByText('unavailable.enable.copyFailed'),
    ).toBeTruthy()
    expect(document.activeElement).toBe(key)
    expect(screen.queryByText('tabs.overview')).toBeNull()

    fireEvent.click(
      screen.getByRole('button', { name: 'unavailable.enable.continue' }),
    )
    expect(await screen.findByText('tabs.overview')).toBeTruthy()
    // This tab is unlocked with the key root just set.
    expect(getManagementKey(BASE_URL)).toBe(key.value)
    await waitFor(() => expect(accessedWith(key.value)).toBe(true))
    expect(screen.getByText('management.editing')).toBeTruthy()
  })

  it('opens the gateway right after the restart when no key was generated', async () => {
    state.get.mockResolvedValueOnce(file()).mockResolvedValue(restarted())
    state.restart.mockImplementation(async () => {
      state.gatewayOn = true
      return { supported: true, instance_id: 'old', restarting: true }
    })
    renderGateway()
    await confirmEnable()
    expect(await screen.findByText('tabs.overview')).toBeTruthy()
    expect(getManagementKey(BASE_URL)).toBe('')
    expect(JSON.parse(state.save.mock.calls[0][0]).gateway).not.toHaveProperty(
      'management_key',
    )
  })

  it('saves without restarting and keeps the key and the restart step on screen', async () => {
    state.get.mockResolvedValue(
      file({
        restart: { supported: false, instance_id: 'old', restarting: false },
      }),
    )
    renderGateway()
    expect(
      await screen.findByText('unavailable.enable.descriptionManualRestart'),
    ).toBeTruthy()
    await confirmEnable({ protect: true })

    const key = await screen.findByLabelText<HTMLInputElement>(
      'unavailable.enable.keyTitle',
    )
    expect(key.value).toMatch(/^ovgm_/)
    expect(screen.getByText('unavailable.enable.keyOnceManual')).toBeTruthy()
    expect(screen.getByText('unavailable.enable.savedRestart')).toBeTruthy()
    expect(state.save).toHaveBeenCalledOnce()
    expect(state.restart).not.toHaveBeenCalled()
    expect(
      screen.queryByRole('button', { name: 'unavailable.enable.action' }),
    ).toBeNull()

    fireEvent.click(
      screen.getByRole('button', { name: 'unavailable.enable.keySaved' }),
    )
    expect(screen.queryByLabelText('unavailable.enable.keyTitle')).toBeNull()
    expect(screen.getByText('unavailable.enable.savedRestart')).toBeTruthy()
    // The server still runs without the key; root enters it after restarting.
    expect(getManagementKey(BASE_URL)).toBe('')
  })

  it('says the gateway is still off when it is off after the restart', async () => {
    state.get.mockResolvedValueOnce(file()).mockResolvedValue(restarted())
    renderGateway()
    await confirmEnable()
    expect(await screen.findByText('unavailable.enable.stillOff')).toBeTruthy()
    expect(screen.queryByText('tabs.overview')).toBeNull()
  })

  it('keeps the key on screen when the gateway is still off after the restart', async () => {
    state.get.mockResolvedValueOnce(file()).mockResolvedValue(restarted())
    renderGateway()
    await confirmEnable({ protect: true })
    const key = await screen.findByLabelText<HTMLInputElement>(
      'unavailable.enable.keyTitle',
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'unavailable.enable.continue' }),
    )
    expect(await screen.findByText('unavailable.enable.stillOff')).toBeTruthy()
    expect(
      screen.getByLabelText<HTMLInputElement>('unavailable.enable.keyTitle')
        .value,
    ).toBe(key.value)
    expect(getManagementKey(BASE_URL)).toBe('')
  })

  it('shows the saved key when the restart cannot be confirmed', async () => {
    state.restart.mockRejectedValue(new Error('connection reset'))
    renderGateway()
    await confirmEnable({ protect: true })
    expect(
      await screen.findByText('unavailable.enable.restartFailed'),
    ).toBeTruthy()
    const key = screen.getByLabelText<HTMLInputElement>(
      'unavailable.enable.keyTitle',
    )
    expect(key.value).toBe(
      JSON.parse(state.save.mock.calls[0][0]).gateway.management_key,
    )
    expect(getManagementKey(BASE_URL)).toBe('')
  })

  it('explains the deployment settings when ov.conf is read-only', async () => {
    state.get.mockResolvedValue(file({ writable: false }))
    renderGateway()
    expect(await screen.findByText('unavailable.enable.readOnly')).toBeTruthy()
    expect(screen.getByText('OPENVIKING_GATEWAY_ENABLED=true')).toBeTruthy()
    expect(screen.getByText(/enabled: true/)).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: 'unavailable.enable.action' }),
    ).toBeNull()
    expect(
      screen.getByText('docs.link').closest('a')?.getAttribute('href'),
    ).toBe(gatewayDocsUrl('enableGateway', 'en'))
  })

  it('reports a rejected configuration that the server rolled back', async () => {
    state.get
      .mockResolvedValueOnce(file())
      .mockResolvedValue(restarted({ rolled_back: true }))
    renderGateway()
    await confirmEnable({ protect: true })
    expect(
      await screen.findByText('unavailable.enable.rolledBack'),
    ).toBeTruthy()
    expect(screen.queryByLabelText('unavailable.enable.keyTitle')).toBeNull()
    expect(getManagementKey(BASE_URL)).toBe('')
  })

  it('asks to reload when ov.conf changed since it was read', async () => {
    state.save.mockRejectedValue(
      new Error('ov.conf changed; reload before saving'),
    )
    renderGateway()
    await confirmEnable()
    expect(await screen.findByText('unavailable.enable.conflict')).toBeTruthy()
    fireEvent.click(
      screen.getByRole('button', { name: 'unavailable.enable.reload' }),
    )
    await waitFor(() => expect(state.get).toHaveBeenCalledTimes(2))
    expect(state.restart).not.toHaveBeenCalled()
  })

  it.each(['admin', 'user'])(
    'asks %s to contact the deployer without an enable button',
    async (role) => {
      state.role = role
      renderGateway()
      expect(await screen.findByText('unavailable.contact')).toBeTruthy()
      expect(
        screen.queryByRole('button', { name: 'unavailable.enable.action' }),
      ).toBeNull()
      expect(state.get).not.toHaveBeenCalled()
    },
  )

  it('shows the manual steps when ov.conf cannot be read', async () => {
    state.get.mockRejectedValue(new Error('forbidden'))
    renderGateway()
    expect(
      await screen.findByText('unavailable.notEnabled.description'),
    ).toBeTruthy()
    expect(screen.getByText(/"enabled":true/)).toBeTruthy()
    expect(
      screen.getByText('docs.link').closest('a')?.getAttribute('href'),
    ).toBe(gatewayDocsUrl('enableGateway', 'en'))
  })
})
