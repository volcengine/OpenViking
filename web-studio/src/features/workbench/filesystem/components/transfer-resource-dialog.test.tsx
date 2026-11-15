// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'

import { TransferResourceDialog } from '#/features/workbench/filesystem/components/transfer-resource-dialog'
import { transferSharedResource } from '#/features/workbench/resources/lib/api'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('#/features/workbench/resources/lib/api', () => ({
  transferSharedResource: vi.fn(),
}))

vi.mock('#/shared/lib/error-toast', () => ({
  showErrorToast: vi.fn(),
}))

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

it('copies recursively to the default target and waits before refreshing', async () => {
  let completeCopy!: () => void
  vi.mocked(transferSharedResource).mockReturnValueOnce(
    new Promise((resolve) => {
      completeCopy = () => resolve({ semanticStatus: 'queued' })
    }),
  )
  const user = userEvent.setup()
  const onCompleted = vi.fn()
  const onOpenChange = vi.fn()
  render(
    <TransferResourceDialog
      open
      operation="copy"
      uri="viking://resources/project/"
      isDirectory
      onCompleted={onCompleted}
      onOpenChange={onOpenChange}
    />,
  )

  const confirm = screen.getByRole('button', {
    name: 'transferResource.copyConfirm',
  })
  expect(confirm).toHaveProperty('disabled', false)
  await user.click(confirm)
  expect(transferSharedResource).toHaveBeenCalledWith(
    'copy',
    'viking://resources/project',
    'viking://resources/project-copy',
    true,
  )
  expect(onCompleted).not.toHaveBeenCalled()
  expect(
    screen.getByRole('button', { name: 'transferResource.submitting' }),
  ).toHaveProperty('disabled', true)

  completeCopy()
  await waitFor(() =>
    expect(onCompleted).toHaveBeenCalledWith('viking://resources/project-copy'),
  )
  expect(onOpenChange).toHaveBeenCalledWith(false)
})

it('requires a different shared target before moving', async () => {
  vi.mocked(transferSharedResource).mockResolvedValueOnce({})
  const user = userEvent.setup()
  const onCompleted = vi.fn()
  render(
    <TransferResourceDialog
      open
      operation="move"
      uri="viking://resources/old-name.md"
      isDirectory={false}
      onCompleted={onCompleted}
      onOpenChange={vi.fn()}
    />,
  )

  const confirm = screen.getByRole('button', {
    name: 'transferResource.moveConfirm',
  })
  expect(confirm).toHaveProperty('disabled', true)
  await user.clear(screen.getByLabelText('transferResource.targetLabel'))
  await user.type(
    screen.getByLabelText('transferResource.targetLabel'),
    'viking://agent/tools/not-allowed',
  )
  expect(confirm).toHaveProperty('disabled', true)
  await user.clear(screen.getByLabelText('transferResource.targetLabel'))
  await user.type(
    screen.getByLabelText('transferResource.targetLabel'),
    'viking://resources/new-name.md',
  )
  expect(screen.getByLabelText('transferResource.targetLabel')).toHaveProperty(
    'value',
    'viking://resources/new-name.md',
  )
  await waitFor(() => expect(confirm).toHaveProperty('disabled', false))
  await user.click(confirm)

  await waitFor(() =>
    expect(transferSharedResource).toHaveBeenCalledWith(
      'move',
      'viking://resources/old-name.md',
      'viking://resources/new-name.md',
      false,
    ),
  )
  expect(onCompleted).toHaveBeenCalledWith('viking://resources/new-name.md')
})

it('keeps the dialog open when the transfer fails', async () => {
  vi.mocked(transferSharedResource).mockRejectedValueOnce(new Error('conflict'))
  const user = userEvent.setup()
  const onCompleted = vi.fn()
  const onOpenChange = vi.fn()
  render(
    <TransferResourceDialog
      open
      operation="copy"
      uri="viking://resources/project"
      isDirectory
      onCompleted={onCompleted}
      onOpenChange={onOpenChange}
    />,
  )

  await user.click(
    screen.getByRole('button', { name: 'transferResource.copyConfirm' }),
  )
  await waitFor(() => expect(transferSharedResource).toHaveBeenCalledOnce())
  expect(onCompleted).not.toHaveBeenCalled()
  expect(onOpenChange).not.toHaveBeenCalledWith(false)
  expect(
    screen.getByRole('button', { name: 'transferResource.copyConfirm' }),
  ).toHaveProperty('disabled', false)
})
