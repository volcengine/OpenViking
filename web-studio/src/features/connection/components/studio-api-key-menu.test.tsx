// @vitest-environment jsdom
import { StudioApiKeyMenu } from '#/features/connection/components/studio-api-key-menu'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  connection: {
    baseUrl: 'https://service.test',
    serviceSelection: 'opensource',
    accountId: 'team',
    userId: 'alice',
    apiKey: 'ov-user-secret-1234',
    adminApiKey: 'ov-root-secret-5678',
  },
  role: 'root',
  copy: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}))
vi.mock('#/features/connection/hooks/use-app-connection', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  useAppConnection: () => ({
    connection: state.connection,
    connectionRole: state.role,
    serverMode: 'api_key',
  }),
}))
vi.mock('#/shared/lib/clipboard', () => ({ copyTextToClipboard: state.copy }))
vi.mock('react-i18next', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('sonner', () => ({
  toast: { success: state.success, error: state.error },
}))
beforeEach(() => {
  vi.clearAllMocks()
  state.connection.apiKey = 'ov-user-secret-1234'
  state.connection.adminApiKey = 'ov-root-secret-5678'
  state.connection.userId = 'alice'
  state.role = 'root'
  state.copy.mockResolvedValue(undefined)
})
afterEach(cleanup)
function open() {
  fireEvent.click(screen.getByRole('button', { name: 'apiKey.title' }))
}
it('shows the current User ID with a masked User Key, without putting either full key into the DOM', () => {
  render(<StudioApiKeyMenu managementMode={false} />)
  open()
  expect(screen.getByText('alice')).toBeTruthy()
  expect(screen.getByText('ov-u…1234')).toBeTruthy()
  expect(document.body.innerHTML).not.toContain(state.connection.apiKey)
  expect(document.body.innerHTML).not.toContain(state.connection.adminApiKey)
})
it('reveals only the current User Key on demand and hides it again after closing', async () => {
  render(<StudioApiKeyMenu managementMode={false} />)
  open()
  fireEvent.click(screen.getByRole('button', { name: 'apiKey.show' }))
  expect(screen.getByText(state.connection.apiKey)).toBeTruthy()
  expect(document.body.innerHTML).not.toContain(state.connection.adminApiKey)
  fireEvent.click(screen.getByRole('button', { name: 'apiKey.title' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  open()
  expect(screen.getByText('ov-u…1234')).toBeTruthy()
  expect(document.body.innerHTML).not.toContain(state.connection.apiKey)
})
it('copies the real selected User Key rather than the masked value or Root key', async () => {
  render(<StudioApiKeyMenu managementMode={false} />)
  open()
  fireEvent.click(screen.getByRole('button', { name: 'apiKey.copy' }))
  await waitFor(() =>
    expect(state.copy).toHaveBeenCalledWith(state.connection.apiKey),
  )
  expect(state.copy).not.toHaveBeenCalledWith(state.connection.adminApiKey)
  expect(document.body.innerHTML).not.toContain(state.connection.apiKey)
})
it('shows Root credential type instead of a tenant User ID in management mode', () => {
  render(<StudioApiKeyMenu managementMode />)
  open()
  expect(screen.getByText('apiKey.root')).toBeTruthy()
  expect(screen.getByText('ov-r…5678')).toBeTruthy()
  expect(screen.queryByText('alice')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'apiKey.copy' }))
  expect(state.copy).toHaveBeenCalledWith(state.connection.adminApiKey)
})
it('never falls back to the saved Root key when the workbench has no User key', () => {
  state.connection.apiKey = ''
  render(<StudioApiKeyMenu managementMode={false} />)
  open()
  expect(screen.getByText('apiKey.empty')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'apiKey.show' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'apiKey.copy' })).toBeNull()
  expect(document.body.innerHTML).not.toContain(state.connection.adminApiKey)
})
it('closes and resets visibility immediately when the User or mode changes', async () => {
  const view = render(<StudioApiKeyMenu managementMode={false} />)
  open()
  fireEvent.click(screen.getByRole('button', { name: 'apiKey.show' }))
  state.connection.apiKey = 'ov-bob-secret-9012'
  state.connection.userId = 'bob'
  view.rerender(<StudioApiKeyMenu managementMode={false} />)
  expect(screen.queryByRole('dialog')).toBeNull()
  open()
  expect(screen.getByText('bob')).toBeTruthy()
  expect(document.body.innerHTML).not.toContain(state.connection.apiKey)
  view.rerender(<StudioApiKeyMenu managementMode />)
  expect(screen.queryByRole('dialog')).toBeNull()
  open()
  expect(screen.getByText('apiKey.root')).toBeTruthy()
  expect(document.body.innerHTML).not.toContain(state.connection.adminApiKey)
})
it('does not expose short keys through masking or raw clipboard errors', async () => {
  state.connection.apiKey = 'short-key'
  state.copy.mockRejectedValue(new Error('short-key failed'))
  render(<StudioApiKeyMenu managementMode={false} />)
  open()
  expect(screen.getByText('••••••••')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'apiKey.copy' }))
  await waitFor(() =>
    expect(state.error).toHaveBeenCalledWith('settings:toast.copyFailed'),
  )
  expect(document.body.innerHTML).not.toContain('short-key')
})
