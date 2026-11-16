// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'

import { DeleteResourceDialog } from '#/features/workbench/filesystem/components/delete-resource-dialog'
import { deleteSharedResource } from '#/features/workbench/resources/lib/api'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}))

vi.mock('#/features/workbench/resources/lib/api', () => ({
  deleteSharedResource: vi.fn(),
}))

vi.mock('#/shared/lib/error-toast', () => ({
  showErrorToast: vi.fn(),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

it('requires the exact URI and waits for recursive deletion before refreshing', async () => {
  let completeDelete!: () => void
  vi.mocked(deleteSharedResource).mockReturnValueOnce(
    new Promise((resolve) => {
      completeDelete = () => resolve({ semanticStatus: 'complete' })
    }),
  )
  const user = userEvent.setup()
  const onDeleted = vi.fn()
  const onOpenChange = vi.fn()
  render(
    <DeleteResourceDialog
      open
      uri="viking://resources/project/"
      isDirectory
      onDeleted={onDeleted}
      onOpenChange={onOpenChange}
    />,
  )

  const confirm = screen.getByRole('button', {
    name: 'deleteResource.confirm',
  })
  expect(confirm).toHaveProperty('disabled', true)
  await user.type(
    screen.getByLabelText('deleteResource.confirmationLabel'),
    'viking://resources/wrong',
  )
  expect(confirm).toHaveProperty('disabled', true)
  await user.clear(screen.getByLabelText('deleteResource.confirmationLabel'))
  await user.type(
    screen.getByLabelText('deleteResource.confirmationLabel'),
    'viking://resources/project',
  )
  expect(confirm).toHaveProperty('disabled', false)
  await user.click(confirm)

  expect(deleteSharedResource).toHaveBeenCalledWith(
    'viking://resources/project',
    true,
  )
  expect(onDeleted).not.toHaveBeenCalled()
  expect(
    screen.getByRole('button', { name: 'deleteResource.deleting' }),
  ).toHaveProperty('disabled', true)

  completeDelete()
  await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce())
  expect(onOpenChange).toHaveBeenCalledWith(false)
})

it('keeps the dialog open and does not refresh when deletion fails', async () => {
  vi.mocked(deleteSharedResource).mockRejectedValueOnce(new Error('denied'))
  const user = userEvent.setup()
  const onDeleted = vi.fn()
  const onOpenChange = vi.fn()
  render(
    <DeleteResourceDialog
      open
      uri="viking://resources/readme.md"
      isDirectory={false}
      onDeleted={onDeleted}
      onOpenChange={onOpenChange}
    />,
  )

  await user.type(
    screen.getByLabelText('deleteResource.confirmationLabel'),
    'viking://resources/readme.md',
  )
  await user.click(
    screen.getByRole('button', { name: 'deleteResource.confirm' }),
  )

  await waitFor(() => expect(deleteSharedResource).toHaveBeenCalledOnce())
  expect(onDeleted).not.toHaveBeenCalled()
  expect(onOpenChange).not.toHaveBeenCalledWith(false)
  expect(
    screen.getByRole('button', { name: 'deleteResource.confirm' }),
  ).toHaveProperty('disabled', false)
})
