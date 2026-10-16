// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type * as Api from '../-lib/api'
import { GatewayError } from '../-lib/api'
import {
  getManagementKey,
  lockManagementKey,
  requestManagementUnlock,
  setManagementKey,
} from '../-lib/management-key'
import { ManagementKeyDialog } from './management-key-dialog'

const state = vi.hoisted(() => ({
  getAccess: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}))
vi.mock('../-lib/api', async (original) => ({
  ...(await original<typeof Api>()),
  getAccess: state.getAccess,
}))
vi.mock('../-lib/use-gateway', () => ({
  useGateway: () => ({
    connection: {
      baseUrl: 'https://gateway.example.com',
      accountId: 'acme',
      userId: 'alice',
      apiKey: 'ov-key',
    },
  }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('sonner', () => ({
  toast: { success: state.success, error: state.error },
}))

beforeEach(() => {
  sessionStorage.clear()
  vi.clearAllMocks()
})
afterEach(cleanup)
function open() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <ManagementKeyDialog />
    </QueryClientProvider>,
  )
  requestManagementUnlock()
}
it('rejects client keys in the management field without sending them', async () => {
  open()
  const input = await screen.findByLabelText('management.label')
  fireEvent.change(input, { target: { value: 'ovgw_client_secret' } })
  expect(screen.getByText('management.wrongKey')).toBeTruthy()
  expect(
    screen.getByRole<HTMLButtonElement>('button', {
      name: 'management.unlock',
    }).disabled,
  ).toBe(true)
  expect(state.getAccess).not.toHaveBeenCalled()
})
it('keeps incorrect candidates out of session storage, then unlocks after server verification', async () => {
  state.getAccess
    .mockRejectedValueOnce(
      new GatewayError('bad', 401, undefined, 'management_key_invalid'),
    )
    .mockResolvedValueOnce({ can_write_objects: true })
  open()
  const input = await screen.findByLabelText('management.label')
  fireEvent.change(input, { target: { value: 'wrong-key' } })
  fireEvent.click(screen.getByRole('button', { name: 'management.unlock' }))
  expect(await screen.findByText('management.invalid')).toBeTruthy()
  expect(getManagementKey('https://gateway.example.com')).toBe('')
  fireEvent.change(input, { target: { value: 'ovgm-correct' } })
  fireEvent.click(screen.getByRole('button', { name: 'management.unlock' }))
  await waitFor(() =>
    expect(getManagementKey('https://gateway.example.com')).toBe(
      'ovgm-correct',
    ),
  )
  expect(state.getAccess).toHaveBeenLastCalledWith(
    expect.objectContaining({ managementKey: 'ovgm-correct' }),
  )
  expect(state.success).toHaveBeenCalledWith('management.unlocked')
})
it('reports automatic locking only when a stored management key expires', () => {
  lockManagementKey('https://gateway.example.com', true)
  expect(state.error).not.toHaveBeenCalled()
  setManagementKey('https://gateway.example.com', 'ovgm-old')
  lockManagementKey('https://gateway.example.com', true)
  expect(getManagementKey('https://gateway.example.com')).toBe('')
  expect(state.error).toHaveBeenCalledTimes(1)
})
it('tolerates browsers that reject session storage', () => {
  const getter = vi
    .spyOn(Storage.prototype, 'getItem')
    .mockImplementation(() => {
      throw new Error('storage disabled')
    })
  expect(getManagementKey('https://gateway.example.com')).toBe('')
  getter.mockRestore()
})

it('stores the submitted key even when the input changes during verification', async () => {
  let resolveAccess!: (access: { can_write_objects: boolean }) => void
  state.getAccess.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveAccess = resolve
      }),
  )
  open()
  const input = await screen.findByLabelText('management.label')
  fireEvent.change(input, { target: { value: 'ovgm-submitted' } })
  fireEvent.click(screen.getByRole('button', { name: 'management.unlock' }))
  await waitFor(() => expect(state.getAccess).toHaveBeenCalledOnce())
  fireEvent.change(input, { target: { value: 'ovgm-unverified-edit' } })
  resolveAccess({ can_write_objects: true })
  await waitFor(() =>
    expect(getManagementKey('https://gateway.example.com')).toBe(
      'ovgm-submitted',
    ),
  )
})
