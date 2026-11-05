// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from 'sonner'
import { afterEach, expect, it, vi } from 'vitest'

import { DataManagementDialog } from '#/features/workbench/filesystem/components/data-management-dialog'
import { importOvpack } from '#/features/workbench/filesystem/lib/data-management'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('sonner', () => ({
  toast: { success: vi.fn() },
}))

vi.mock('#/features/workbench/filesystem/lib/data-management', () => ({
  createDataManagementClient: () => ({ client: {} }),
  exportOvpack: vi.fn(),
  importOvpack: vi.fn(),
  listSnapshots: vi.fn(),
}))

afterEach(cleanup)

it('shows a loading button, then closes and refreshes after a successful import', async () => {
  let completeImport!: (value: { uri: string }) => void
  vi.mocked(importOvpack).mockReturnValueOnce(
    new Promise((resolve) => {
      completeImport = resolve
    }),
  )
  const user = userEvent.setup()
  const onClose = vi.fn()
  const onChanged = vi.fn()
  render(
    <DataManagementDialog
      mode="transfer"
      uri="viking://resources/project/"
      isDirectory
      onClose={onClose}
      onCompare={vi.fn()}
      onChanged={onChanged}
    />,
  )

  await user.click(screen.getByRole('tab', { name: 'importTab' }))
  await user.upload(
    screen.getByLabelText('chooseFile'),
    new File(['sample'], 'project.ovpack'),
  )
  await user.click(screen.getByRole('button', { name: 'importAction' }))

  expect(onClose).not.toHaveBeenCalled()
  expect(onChanged).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: 'importing' })).toHaveProperty(
    'disabled',
    true,
  )

  completeImport({ uri: 'viking://resources/project/' })
  await waitFor(() => expect(onClose).toHaveBeenCalledOnce())
  await waitFor(() => expect(onChanged).toHaveBeenCalledOnce())
  expect(toast.success).toHaveBeenCalledWith('imported')
})

it('keeps the import tab open with the error and does not refresh on failure', async () => {
  vi.mocked(importOvpack).mockRejectedValueOnce(new Error('Import failed'))
  const user = userEvent.setup()
  const onClose = vi.fn()
  const onChanged = vi.fn()
  render(
    <DataManagementDialog
      mode="transfer"
      uri="viking://resources/project/"
      isDirectory
      onClose={onClose}
      onCompare={vi.fn()}
      onChanged={onChanged}
    />,
  )

  await user.click(screen.getByRole('tab', { name: 'importTab' }))
  await user.upload(
    screen.getByLabelText('chooseFile'),
    new File(['sample'], 'project.ovpack'),
  )
  await user.click(screen.getByRole('button', { name: 'importAction' }))

  expect(await screen.findByRole('alert')).toHaveProperty(
    'textContent',
    'Import failed',
  )
  expect(
    screen
      .getByRole('tab', { name: 'importTab' })
      .getAttribute('aria-selected'),
  ).toBe('true')
  expect(onClose).not.toHaveBeenCalled()
  expect(onChanged).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: 'importAction' })).toHaveProperty(
    'disabled',
    false,
  )
})

it('keeps the selected OVPack file while switching between import and export', async () => {
  const user = userEvent.setup()
  render(
    <DataManagementDialog
      mode="transfer"
      uri="viking://resources/project/"
      isDirectory
      onClose={vi.fn()}
      onCompare={vi.fn()}
      onChanged={vi.fn()}
    />,
  )

  expect(screen.getByRole('button', { name: 'exportAction' })).toBeTruthy()
  await user.click(screen.getByRole('tab', { name: 'importTab' }))
  const importButton = screen.getByRole('button', { name: 'importAction' })
  expect(importButton.disabled).toBe(true)
  expect(screen.getByText('chooseFile')).toBeTruthy()

  await user.upload(
    screen.getByLabelText('chooseFile'),
    new File(['sample'], 'project.ovpack', { type: 'application/zip' }),
  )
  expect(importButton.disabled).toBe(false)
  expect(screen.getByText('project.ovpack')).toBeTruthy()
  expect(screen.getByText('changeFile')).toBeTruthy()

  await user.click(screen.getByRole('tab', { name: 'exportTab' }))
  await user.click(screen.getByRole('tab', { name: 'importTab' }))
  expect(importButton.disabled).toBe(false)
  expect(screen.getByLabelText('chooseFile')).toHaveProperty(
    'files',
    expect.objectContaining({ length: 1 }),
  )
})
