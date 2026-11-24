// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import i18n from '#/i18n'
import { ManagementKeyError } from '../lib/management-key'
import { ManagementKeyDialog } from './management-key-dialog'

const mocks = vi.hoisted(() => ({
  connection: {
    baseUrl: 'http://ov.test',
    accountId: 'a',
    userId: 'alice',
    apiKey: 'alice-key',
    adminApiKey: '',
    serviceSelection: 'opensource',
  },
  verify: vi.fn(),
  save: vi.fn(),
  flush: vi.fn(),
  navigate: vi.fn(),
  toast: vi.fn(),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: mocks.connection,
    saveConnection: mocks.save,
  }),
}))
vi.mock('#/features/connection/lib/management-key', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  verifyManagementKey: mocks.verify,
}))
vi.mock('#/features/connection/lib/credential-storage', () => ({
  flushCredentialStorage: mocks.flush,
}))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => mocks.navigate }))
vi.mock('sonner', () => ({ toast: { error: mocks.toast } }))
beforeEach(async () => {
  vi.clearAllMocks()
  await i18n.changeLanguage('zh-CN')
  mocks.verify.mockResolvedValue('control-key')
  mocks.flush.mockResolvedValue(undefined)
})
afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('en')
})
async function open() {
  render(<ManagementKeyDialog />)
  fireEvent.click(screen.getByRole('button', { name: '管理后台' }))
  await screen.findByRole('dialog', { name: '配置管理权限' })
}

it('keeps the entry available without credentials and opens guidance without making a management request', async () => {
  await open()
  expect(screen.getByText('http://ov.test')).toBeTruthy()
  expect(
    screen.getByText(/Account Admin 仅管理该 Key 所属的 Account/),
  ).toBeTruthy()
  expect(mocks.verify).not.toHaveBeenCalled()
  expect(mocks.save).not.toHaveBeenCalled()
  expect(
    screen
      .getByRole('button', {
        name: '验证并进入管理后台',
      })
      .hasAttribute('disabled'),
  ).toBe(true)
})

it('saves the verified control key without changing the User identity and waits for persistence before entering', async () => {
  await open()
  let finish!: () => void
  mocks.flush.mockReturnValue(
    new Promise<void>((resolve) => {
      finish = resolve
    }),
  )
  fireEvent.change(screen.getByLabelText('管理 API Key'), {
    target: { value: 'control-key' },
  })
  fireEvent.click(screen.getByRole('button', { name: '验证并进入管理后台' }))
  await waitFor(() =>
    expect(mocks.save).toHaveBeenCalledWith({
      ...mocks.connection,
      adminApiKey: 'control-key',
    }),
  )
  expect(mocks.navigate).not.toHaveBeenCalled()
  await act(async () => finish())
  await waitFor(() =>
    expect(mocks.navigate).toHaveBeenCalledWith({ to: '/admin' }),
  )
})

it('rejects an ordinary User key with actionable guidance, preserving all saved credentials', async () => {
  mocks.verify.mockRejectedValue(new ManagementKeyError('notAdmin'))
  await open()
  fireEvent.change(screen.getByLabelText('管理 API Key'), {
    target: { value: 'ordinary-key' },
  })
  fireEvent.click(screen.getByRole('button', { name: '验证并进入管理后台' }))
  expect((await screen.findByRole('alert')).textContent).toContain(
    '这是普通 User API Key',
  )
  expect(mocks.save).not.toHaveBeenCalled()
  expect(mocks.navigate).not.toHaveBeenCalled()
  await act(() => i18n.changeLanguage('en'))
  expect(screen.getByRole('alert').textContent).toBe(
    i18n.t('studio:managementKey.notAdmin'),
  )
})

it('aborts validation on cancel and never saves a late response', async () => {
  let finish!: (key: string) => void
  mocks.verify.mockReturnValue(
    new Promise<string>((resolve) => {
      finish = resolve
    }),
  )
  await open()
  fireEvent.change(screen.getByLabelText('管理 API Key'), {
    target: { value: 'control-key' },
  })
  fireEvent.click(screen.getByRole('button', { name: '验证并进入管理后台' }))
  const signal = mocks.verify.mock.calls[0][2] as AbortSignal
  fireEvent.click(screen.getByRole('button', { name: '取消' }))
  expect(signal.aborted).toBe(true)
  await act(async () => finish('control-key'))
  expect(mocks.save).not.toHaveBeenCalled()
  expect(mocks.navigate).not.toHaveBeenCalled()
})
