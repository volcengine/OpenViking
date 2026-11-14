// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { toast } from 'sonner'
import { afterEach, expect, it, vi } from 'vitest'

import { DataManagementDialog } from '#/features/workbench/filesystem/components/data-management-dialog'
import {
  commitSnapshot,
  importOvpack,
  listSnapshots,
} from '#/features/workbench/filesystem/lib/data-management'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('sonner', () => ({
  toast: { success: vi.fn() },
}))

vi.mock('#/features/workbench/filesystem/lib/data-management', () => ({
  commitSnapshot: vi.fn(),
  createDataManagementClient: () => ({ client: {} }),
  exportOvpack: vi.fn(),
  importOvpack: vi.fn(),
  listSnapshots: vi.fn(),
}))

afterEach(cleanup)

const scanLimitError = new Error(
  'snapshot log scan limit exceeded: scanned 1000/1000 commits, matched 0/20',
)

function renderHistory() {
  const onChanged = vi.fn()
  render(
    <DataManagementDialog
      mode="history"
      uri="viking://resources/guide.md"
      isDirectory={false}
      onClose={vi.fn()}
      onCompare={vi.fn()}
      onChanged={onChanged}
    />,
  )
  return onChanged
}

it('ends loading and reports incomplete history at the scan limit', async () => {
  vi.mocked(listSnapshots).mockRejectedValueOnce(scanLimitError)
  renderHistory()

  expect(screen.getByRole('status').textContent).toBe('loading')
  expect((await screen.findByRole('alert')).textContent).toBe(
    'historyScanLimit',
  )
  expect(screen.queryByText('loading')).toBeNull()
  expect(screen.queryByText('noSnapshots')).toBeNull()
})

it('reports a successful save even when refreshing history reaches the scan limit', async () => {
  vi.mocked(listSnapshots)
    .mockResolvedValueOnce([])
    .mockRejectedValueOnce(scanLimitError)
  vi.mocked(commitSnapshot).mockResolvedValueOnce({
    result: 'ok',
    commit_oid: 'saved',
  })
  const onChanged = renderHistory()
  await screen.findByText('noSnapshots')

  const user = userEvent.setup()
  await user.type(
    screen.getByRole('textbox', { name: 'message' }),
    'My snapshot',
  )
  await user.click(screen.getByRole('button', { name: 'saveSnapshot' }))

  expect((await screen.findByRole('alert')).textContent).toBe(
    'historyScanLimit',
  )
  expect(toast.success).toHaveBeenCalledWith('snapshotSaved')
  expect(onChanged).toHaveBeenCalledOnce()
  expect(screen.getByRole('textbox', { name: 'message' })).toHaveProperty(
    'value',
    '',
  )
})

it('offers only export when the directory cannot be written', () => {
  render(
    <DataManagementDialog
      mode="transfer"
      initialTransferTab="import"
      allowImport={false}
      uri="viking://resources/project/"
      isDirectory
      onClose={vi.fn()}
      onCompare={vi.fn()}
      onChanged={vi.fn()}
    />,
  )

  expect(screen.getByRole('button', { name: 'exportAction' })).toBeTruthy()
  expect(screen.queryByRole('tab', { name: 'importTab' })).toBeNull()
  expect(screen.queryByLabelText('chooseFile')).toBeNull()
})

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

it.each(['import', 'export'] as const)(
  'opens the requested %s tab',
  (initialTransferTab) => {
    render(
      <DataManagementDialog
        mode="transfer"
        initialTransferTab={initialTransferTab}
        uri="viking://resources/project/"
        isDirectory
        onClose={vi.fn()}
        onCompare={vi.fn()}
        onChanged={vi.fn()}
      />,
    )
    expect(
      screen
        .getByRole('tab', {
          name: initialTransferTab === 'import' ? 'importTab' : 'exportTab',
        })
        .getAttribute('aria-selected'),
    ).toBe('true')
  },
)
