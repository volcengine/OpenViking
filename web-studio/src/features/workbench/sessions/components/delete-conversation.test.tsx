// @vitest-environment jsdom
import { DeleteConversation } from '#/features/workbench/sessions/components/delete-conversation'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const remove = vi.hoisted(() => vi.fn())
vi.mock('#/features/workbench/sessions/lib/use-sessions', () => ({
  useDeleteSession: () => ({
    mutateAsync: remove,
    reset: vi.fn(),
    isPending: false,
    error: null,
  }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

it('requires confirmation and does not delete on cancel', async () => {
  const onDeleted = vi.fn()
  remove.mockResolvedValue(undefined)
  render(<DeleteConversation id="session" title="Chat" onDeleted={onDeleted} />)
  fireEvent.click(
    screen.getByRole('button', { name: 'threadList.deleteSession' }),
  )
  expect(remove).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'threadList.cancel' }))
  expect(remove).not.toHaveBeenCalled()
  fireEvent.click(
    screen.getByRole('button', { name: 'threadList.deleteSession' }),
  )
  fireEvent.click(
    screen.getByRole('button', { name: 'threadList.confirmDelete' }),
  )
  await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce())
  expect(remove).toHaveBeenCalledExactlyOnceWith('session')
})

it('does not dismiss the conversation when deletion fails', async () => {
  const onDeleted = vi.fn()
  remove.mockRejectedValue(new Error('failed'))
  render(<DeleteConversation id="session" title="Chat" onDeleted={onDeleted} />)
  fireEvent.click(
    screen.getByRole('button', { name: 'threadList.deleteSession' }),
  )
  fireEvent.click(
    screen.getByRole('button', { name: 'threadList.confirmDelete' }),
  )
  await waitFor(() => expect(remove).toHaveBeenCalledOnce())
  expect(onDeleted).not.toHaveBeenCalled()
  expect(screen.getByRole('alertdialog')).toBeTruthy()
})
