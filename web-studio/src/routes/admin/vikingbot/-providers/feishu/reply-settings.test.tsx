// @vitest-environment jsdom
import type { Connection } from '#/features/admin/vikingbot/api'
import { ReplySettings } from '#/features/admin/vikingbot/providers/feishu/reply-settings'
import zh from '#/i18n/locales/zh-CN/vikingbot'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const update = vi.hoisted(() => vi.fn())
vi.mock('#/features/admin/vikingbot/api', () => ({
  updateConnectionSettings: update,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: keyof typeof zh) => zh[key] }),
}))
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})
it('keeps a failed selection for retry and saves against the connection revision', async () => {
  const connection: Connection = {
    id: 'c',
    app_id: 'cli_test',
    bot_name: 'Bot',
    enabled: true,
    step: 4,
    revision: 3,
    identity_user: 'bot',
    status: { state: 'connected' },
  }
  const saved = vi.fn()
  update.mockRejectedValueOnce(new Error('Rejected')).mockResolvedValueOnce({})
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { mutations: { retry: false } } })
      }
    >
      <ReplySettings connection={connection} onSaved={saved} />
    </QueryClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: zh.replyMode }))
  const select = screen.getByRole<HTMLSelectElement>('combobox', {
    name: zh.replyMode,
  })
  expect(select.value).toBe('true')
  expect(
    screen.getByRole<HTMLButtonElement>('button', { name: zh.saveReplyMode })
      .disabled,
  ).toBe(true)
  fireEvent.change(select, { target: { value: 'false' } })
  fireEvent.click(screen.getByRole('button', { name: zh.saveReplyMode }))
  await screen.findByRole('alert')
  expect(select.value).toBe('false')
  expect(saved).not.toHaveBeenCalled()
  expect(update).toHaveBeenCalledWith(connection, {
    thread_require_mention: false,
  })
  fireEvent.click(screen.getByRole('button', { name: zh.saveReplyMode }))
  await waitFor(() => expect(saved).toHaveBeenCalledTimes(1))
})
