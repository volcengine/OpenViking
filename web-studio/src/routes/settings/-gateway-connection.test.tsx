// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { GatewayConnectionSettings } from './route'

const { health, saveConnection, navigate, connectionState } = vi.hoisted(
  () => ({
    health: vi.fn(),
    saveConnection: vi.fn(),
    navigate: vi.fn(),
    connectionState: {
      connection: {
        baseUrl: 'http://gateway.test',
        apiKey: '',
        adminApiKey: '',
        accountId: 'default',
        userId: 'default',
      },
      openvikingUrl: 'https://openviking.example',
      serverMode: 'api_key',
      isGatewayHost: true,
    },
  }),
)
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ ...connectionState, saveConnection }),
}))
vi.mock('#/lib/connection/health', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  fetchServerHealth: health,
}))
vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useNavigate: () => navigate,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: { url?: string }) =>
      values?.url ? `${key}: ${values.url}` : key,
  }),
}))
afterEach(cleanup)
beforeEach(() => vi.clearAllMocks())

it('shows one key input and logs in with the OV key against the gateway host', async () => {
  health.mockResolvedValue({ role: 'user', account_id: 'a', user_id: 'alice' })
  const { container } = render(<GatewayConnectionSettings />)
  expect(container.querySelectorAll('input')).toHaveLength(1)
  expect(
    screen.getByText(
      'gatewayConnection.openvikingUrl: https://openviking.example',
    ),
  ).toBeTruthy()
  fireEvent.change(screen.getByLabelText('gatewayConnection.apiKey'), {
    target: { value: 'ov-user-key' },
  })
  fireEvent.click(screen.getByText('gatewayConnection.connect'))
  await waitFor(() => expect(navigate).toHaveBeenCalledWith({ to: '/gateway' }))
  expect(health).toHaveBeenCalledWith('http://gateway.test', {
    'X-API-Key': 'ov-user-key',
  })
  expect(saveConnection).toHaveBeenCalledWith({
    ...connectionState.connection,
    apiKey: 'ov-user-key',
    accountId: 'a',
    userId: 'alice',
  })
})

it.each(['ovgw_client', 'ovgm_management'])(
  'rejects a misplaced %s key without sending it',
  (apiKey) => {
    render(<GatewayConnectionSettings />)
    fireEvent.change(screen.getByLabelText('gatewayConnection.apiKey'), {
      target: { value: apiKey },
    })
    expect(screen.getByRole('alert').textContent).toBe(
      'gatewayConnection.wrongKey',
    )
    fireEvent.click(screen.getByText('gatewayConnection.connect'))
    expect(health).not.toHaveBeenCalled()
  },
)

function stubAccess(body: object) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ json: () => Promise.resolve(body) }),
  )
}
afterEach(() => vi.unstubAllGlobals())

it('keeps the current connection when health omits the identity', async () => {
  health.mockResolvedValue({ service: 'openviking-gateway' })
  stubAccess({ reason: 'openviking_http_401' })
  render(<GatewayConnectionSettings />)
  fireEvent.change(screen.getByLabelText('gatewayConnection.apiKey'), {
    target: { value: 'invalid' },
  })
  fireEvent.click(screen.getByText('gatewayConnection.connect'))
  await waitFor(() =>
    expect(screen.getByRole('alert').textContent).toBe(
      'gatewayConnection.invalidKey',
    ),
  )
  expect(saveConnection).not.toHaveBeenCalled()
  expect(navigate).not.toHaveBeenCalled()
})

it('says OpenViking is down instead of blaming the key', async () => {
  health.mockResolvedValue({ service: 'openviking-gateway' })
  stubAccess({ reason: 'openviking_unreachable' })
  render(<GatewayConnectionSettings />)
  fireEvent.change(screen.getByLabelText('gatewayConnection.apiKey'), {
    target: { value: 'valid-but-unverifiable' },
  })
  fireEvent.click(screen.getByText('gatewayConnection.connect'))
  await waitFor(() =>
    expect(screen.getByRole('alert').textContent).toBe(
      'gatewayConnection.unreachable: https://openviking.example',
    ),
  )
  expect(fetch).toHaveBeenCalledWith(
    'http://gateway.test/gateway/admin/access',
    { headers: { 'X-API-Key': 'valid-but-unverifiable' } },
  )
  expect(saveConnection).not.toHaveBeenCalled()
})

it('says the gateway itself is unreachable when health fails', async () => {
  health.mockRejectedValue(new Error('offline'))
  render(<GatewayConnectionSettings />)
  fireEvent.change(screen.getByLabelText('gatewayConnection.apiKey'), {
    target: { value: 'key' },
  })
  fireEvent.click(screen.getByText('gatewayConnection.connect'))
  await waitFor(() =>
    expect(screen.getByRole('alert').textContent).toBe(
      'gatewayConnection.gatewayUnreachable',
    ),
  )
})
