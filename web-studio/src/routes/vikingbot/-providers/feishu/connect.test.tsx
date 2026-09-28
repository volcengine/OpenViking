// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { FeishuConnect } from './connect'
import zh from '#/i18n/locales/zh-CN/vikingbot'

const api = vi.hoisted(() => ({
  current: vi.fn(),
  get: vi.fn(),
  start: vi.fn(),
  update: vi.fn(),
  users: vi.fn(),
  connections: vi.fn(),
}))
vi.mock('./api', () => ({
  currentOnboarding: api.current,
  getOnboarding: api.get,
  startOnboarding: api.start,
  updateOnboarding: api.update,
}))
vi.mock('../../-api', () => ({
  getBotUsers: api.users,
  getConnections: api.connections,
  verifyConnection: vi.fn(),
}))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'a' }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      key.split('.').reduce<any>((value, part) => value?.[part], zh) || key,
  }),
}))
const run = {
  id: 'job',
  type: 'feishu',
  state: 'waiting_for_scan',
  name: 'VikingBot',
  user_id: 'bot',
  qr: '{"qrlogin":{"token":"test-only"}}',
  can_retry: false,
}
beforeEach(() => {
  api.current.mockResolvedValue(null)
  api.get.mockResolvedValue(run)
  api.start.mockResolvedValue(run)
  api.users.mockResolvedValue([{ user_id: 'bot', available: true }])
  api.connections.mockResolvedValue([])
})
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
  vi.unstubAllGlobals()
})
function show() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <FeishuConnect onChange={vi.fn()} onClose={vi.fn()} />
    </QueryClientProvider>,
  )
}
it('selects the sole user and starts without app credentials', async () => {
  show()
  const button = await screen.findByRole('button', { name: zh.qr.start })
  await waitFor(() =>
    expect((button as HTMLButtonElement).disabled).toBe(false),
  )
  expect(screen.queryByLabelText('App Secret')).toBeNull()
  fireEvent.click(button)
  await screen.findByText(zh.qr.states.waiting_for_scan)
  expect(api.start).toHaveBeenCalledWith(
    expect.objectContaining({
      user_id: 'bot',
      name: 'VikingBot',
      request_id: expect.any(String),
      settings: { thread_require_mention: true },
    }),
  )
  expect(screen.getByTitle(zh.qr.scan)).toBeTruthy()
})
it('restores active tasks instead of creating another app', async () => {
  api.current.mockResolvedValue(run)
  show()
  await screen.findByText(zh.qr.states.waiting_for_scan)
  expect(api.start).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: zh.qr.start })).toBeNull()
})
it('locks duplicate start requests', async () => {
  api.start.mockReturnValue(new Promise(() => {}))
  show()
  const button = await screen.findByRole('button', { name: zh.qr.start })
  await waitFor(() =>
    expect((button as HTMLButtonElement).disabled).toBe(false),
  )
  fireEvent.click(button)
  fireEvent.click(button)
  await waitFor(() => expect(api.start).toHaveBeenCalledTimes(1))
})
it('hides expired QR and offers explicit retry', async () => {
  const expired = { ...run, state: 'expired', can_retry: true }
  api.current.mockResolvedValue(expired)
  api.get.mockResolvedValue(expired)
  show()
  await screen.findByText(zh.qr.states.expired)
  expect(screen.queryByTitle(zh.qr.scan)).toBeNull()
  expect(screen.getByRole('button', { name: zh.qr.retryScan })).toBeTruthy()
})
it('does not offer group completion while awaiting approval', async () => {
  const approval = { ...run, qr: undefined, state: 'awaiting_approval' }
  api.current.mockResolvedValue(approval)
  api.get.mockResolvedValue(approval)
  show()
  await screen.findByText(zh.qr.states.awaiting_approval)
  expect(screen.queryByRole('button', { name: zh.test })).toBeNull()
})
it('does not retry uncertain creation', async () => {
  const failed = {
    ...run,
    qr: undefined,
    state: 'failed',
    error: 'creation_uncertain',
    can_retry: false,
  }
  api.current.mockResolvedValue(failed)
  api.get.mockResolvedValue(failed)
  show()
  await screen.findByText(zh.qr.errors.creation_uncertain)
  expect(screen.queryByRole('button', { name: zh.qr.retryScan })).toBeNull()
})

it('works on HTTP dev hosts without crypto.randomUUID', async () => {
  vi.stubGlobal('crypto', {
    getRandomValues: crypto.getRandomValues.bind(crypto),
  })
  show()
  const button = await screen.findByRole('button', { name: zh.qr.start })
  await waitFor(() =>
    expect((button as HTMLButtonElement).disabled).toBe(false),
  )
  fireEvent.click(button)
  await waitFor(() => expect(api.start).toHaveBeenCalledTimes(1))
  expect(api.start.mock.calls[0][0].request_id).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  )
})

it('passes the selected group reply mode to QR onboarding', async () => {
  show()
  const button = await screen.findByRole('button', { name: zh.qr.start })
  await waitFor(() =>
    expect((button as HTMLButtonElement).disabled).toBe(false),
  )
  fireEvent.change(screen.getByLabelText(zh.replyMode), {
    target: { value: 'false' },
  })
  fireEvent.click(button)
  await waitFor(() =>
    expect(api.start).toHaveBeenCalledWith(
      expect.objectContaining({ settings: { thread_require_mention: false } }),
    ),
  )
})

it.each([
  { users: [] },
  { users: [{ user_id: 'unavailable', available: false }] },
])(
  'offers user management and recovers after refreshing unavailable users: %j',
  async ({ users }) => {
    api.users.mockResolvedValueOnce(users)
    show()
    await screen.findByRole('link', { name: zh.manageUsers })
    const submit = screen.getByRole<HTMLButtonElement>('button', {
      name: zh.qr.start,
    })
    expect(submit.disabled).toBe(true)
    api.users.mockResolvedValueOnce([{ user_id: 'recovered', available: true }])
    fireEvent.click(screen.getByRole('button', { name: zh.retry }))
    await waitFor(() =>
      expect(
        screen.getByLabelText<HTMLSelectElement>(zh.runtimeUser).value,
      ).toBe('recovered'),
    )
    expect(submit.disabled).toBe(false)
    expect(screen.queryByRole('link', { name: zh.manageUsers })).toBeNull()
  },
)
