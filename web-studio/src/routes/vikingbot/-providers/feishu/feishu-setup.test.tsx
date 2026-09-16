// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { FeishuSetup } from './feishu-setup'
import zh from '#/i18n/locales/zh-CN/vikingbot'
import type { Connection } from '../../-api'

const api = vi.hoisted(() => ({
  create: vi.fn(),
  update: vi.fn(),
  users: vi.fn().mockResolvedValue([{ user_id: 'bot-user', available: true }]),
}))
vi.mock('../../-api', () => ({
  createConnection: api.create,
  updateConnection: api.update,
  getBotUsers: api.users,
}))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'test' }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => (zh as Record<string, unknown>)[key] || key,
  }),
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
function show(connection?: Connection) {
  const change = vi.fn()
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { mutations: { retry: false } } })
      }
    >
      <FeishuSetup
        connection={connection}
        onChange={change}
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  )
  return change
}
const connection: Connection = {
  id: 'id',
  app_id: 'cli_test',
  bot_name: 'Bot',
  enabled: true,
  step: 4,
  revision: 1,
  identity_user: 'bot',
  status: { state: 'connected' },
}
it('does not allow completion based only on a connected socket', () => {
  show(connection)
  expect(
    screen.getByRole<HTMLButtonElement>('button', { name: zh.visible })
      .disabled,
  ).toBe(true)
})
it('received messages without successful outbound delivery do not finish onboarding', () => {
  show({
    ...connection,
    status: {
      ...connection.status,
      verification: {
        code: 'ABC',
        expires_at: Date.now() / 1000 + 60,
        received: true,
        sent: false,
      },
    },
  })
  expect(
    screen.getByRole<HTMLButtonElement>('button', { name: zh.visible })
      .disabled,
  ).toBe(true)
})
it('allows user confirmation after platform delivery succeeds', () => {
  show({
    ...connection,
    status: {
      ...connection.status,
      verification: {
        code: 'ABC',
        expires_at: Date.now() / 1000 + 60,
        received: true,
        sent: true,
      },
    },
  })
  expect(
    screen.getByRole<HTMLButtonElement>('button', { name: zh.visible })
      .disabled,
  ).toBe(false)
})
it('preserves credentials after validation failure and blocks duplicate submission', async () => {
  let reject!: (error: Error) => void
  api.create.mockImplementation(
    () =>
      new Promise((_resolve, rejectPromise) => {
        reject = rejectPromise
      }),
  )
  const changed = show()
  fireEvent.click(
    screen.getByRole<HTMLButtonElement>('button', { name: zh.next }),
  )
  fireEvent.change(screen.getByLabelText<HTMLInputElement>(zh.appId), {
    target: { value: 'cli_test' },
  })
  fireEvent.change(screen.getByLabelText<HTMLInputElement>(zh.appSecret), {
    target: { value: 'secret' },
  })
  await screen.findByRole('option', { name: 'bot-user' })
  fireEvent.change(screen.getByLabelText<HTMLSelectElement>(zh.runtimeUser), {
    target: { value: 'bot-user' },
  })
  fireEvent.click(
    screen.getByRole<HTMLButtonElement>('button', { name: zh.connect }),
  )
  await waitFor(() => expect(api.create).toHaveBeenCalledTimes(1))
  expect(
    screen.getByRole<HTMLButtonElement>('button', { name: zh.connect })
      .disabled,
  ).toBe(true)
  reject(new Error('Rejected'))
  await screen.findByRole('alert')
  expect(screen.getByLabelText<HTMLInputElement>(zh.appSecret).value).toBe(
    'secret',
  )
  expect(changed).not.toHaveBeenCalled()
})
