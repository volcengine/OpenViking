// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { DeleteDocumentDialog } from './delete-document-dialog'

const m = vi.hoisted(() => ({ remove: vi.fn(), create: vi.fn(), client: {} }))
vi.mock('#/shared/api/ov-client', () => ({
  getOvClient: () => ({
    getOptions: () => ({ baseUrl: 'https://first.test' }),
    getConnection: () => ({ apiKey: 'user-key', adminApiKey: 'admin-key' }),
  }),
  createOvClient: m.create,
  deleteFs: m.remove,
  getOvResult: (response: Promise<unknown>) => response,
  normalizeOvClientError: (error: Error) => error,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, args?: { error?: string }) =>
      args?.error ? `${key}: ${args.error}` : key,
  }),
}))
beforeEach(() => {
  m.create.mockReturnValue({ client: m.client })
  m.remove.mockResolvedValue({})
})
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})
const uri = 'viking://user/default/memories/test.md'
function setup(dirty = false) {
  const onClose = vi.fn(),
    onDeleted = vi.fn()
  const view = render(
    <DeleteDocumentDialog
      uri={uri}
      name="test.md"
      dirty={dirty}
      onClose={onClose}
      onDeleted={onDeleted}
    />,
  )
  return { ...view, onClose, onDeleted, user: userEvent.setup() }
}
it('does not delete before confirmation and allows cancellation', async () => {
  const { user, onClose } = setup(true)
  expect(screen.getByText('deleteUnsavedWarning')).toBeTruthy()
  expect(screen.getByText(uri)).toBeTruthy()
  expect(m.remove).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'deleteCancel' }))
  expect(onClose).toHaveBeenCalledOnce()
  expect(m.remove).not.toHaveBeenCalled()
})
it('deletes only the confirmed file once with a frozen data-plane client', async () => {
  let complete!: () => void
  m.remove.mockReturnValue(
    new Promise<void>((resolve) => {
      complete = resolve
    }),
  )
  const { user, onDeleted } = setup()
  await user.dblClick(screen.getByRole('button', { name: 'deleteConfirm' }))
  expect(m.remove).toHaveBeenCalledOnce()
  expect(m.create).toHaveBeenCalledWith(
    expect.objectContaining({ credentialScope: 'data', bindSdkClient: false }),
  )
  expect(m.remove).toHaveBeenCalledWith(
    expect.objectContaining({
      client: m.client,
      query: { uri, recursive: false },
    }),
  )
  expect(screen.getByRole('button', { name: 'deleteCancel' })).toHaveProperty(
    'disabled',
    true,
  )
  complete()
  await waitFor(() => expect(onDeleted).toHaveBeenCalledWith(uri))
})
it('keeps a permission failure visible and permits retry', async () => {
  m.remove.mockRejectedValueOnce(new Error('Forbidden'))
  const { user, onDeleted } = setup()
  await user.click(screen.getByRole('button', { name: 'deleteConfirm' }))
  expect((await screen.findByRole('alert')).textContent).toContain('Forbidden')
  expect(onDeleted).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'deleteConfirm' }))
  await waitFor(() => expect(onDeleted).toHaveBeenCalledWith(uri))
})
it('aborts on unmount and ignores a late successful response', async () => {
  let complete!: () => void
  m.remove.mockReturnValue(
    new Promise<void>((resolve) => {
      complete = resolve
    }),
  )
  const { user, unmount, onDeleted } = setup()
  await user.click(screen.getByRole('button', { name: 'deleteConfirm' }))
  const signal = m.remove.mock.calls[0][0].signal as AbortSignal
  unmount()
  expect(signal.aborted).toBe(true)
  complete()
  await Promise.resolve()
  expect(onDeleted).not.toHaveBeenCalled()
})
