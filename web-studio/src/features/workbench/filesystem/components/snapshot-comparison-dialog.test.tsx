// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'

import { SnapshotComparisonDialog } from '#/features/workbench/filesystem/components/snapshot-comparison-dialog'

const mocks = vi.hoisted(() => ({
  diffSnapshot: vi.fn(),
  diffSnapshotWithCurrent: vi.fn(),
  listSnapshots: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('#/features/workbench/filesystem/lib/data-management', () => ({
  createDataManagementClient: () => ({}),
  diffSnapshot: mocks.diffSnapshot,
  diffSnapshotWithCurrent: mocks.diffSnapshotWithCurrent,
  listSnapshots: mocks.listSnapshots,
}))

vi.mock(
  '#/features/workbench/filesystem/components/snapshot-diff-viewer',
  () => ({
    SnapshotDiffViewer: ({ diff }: { diff: string }) => <div>{diff}</div>,
  }),
)

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

it('compares on selection and ignores a stale response', async () => {
  mocks.listSnapshots.mockResolvedValue([
    { oid: 'new', message: 'New' },
    { oid: 'old', message: 'Old' },
  ])
  const pending: Array<
    (value: {
      diff: string
      missingBase: boolean
      missingTarget: boolean
    }) => void
  > = []
  mocks.diffSnapshotWithCurrent.mockImplementation(
    () => new Promise((resolve) => pending.push(resolve)),
  )
  mocks.diffSnapshot.mockResolvedValue({
    diff: 'selected comparison',
    missingBase: false,
    missingTarget: false,
  })

  render(
    <SnapshotComparisonDialog
      onClose={vi.fn()}
      uri="viking://resources/note.md"
    />,
  )
  await waitFor(() =>
    expect(mocks.diffSnapshotWithCurrent).toHaveBeenCalledTimes(1),
  )
  expect(mocks.diffSnapshotWithCurrent.mock.calls[0][2]).toBe('new')

  const user = userEvent.setup()
  await user.click(screen.getByRole('combobox', { name: 'selectedVersion' }))
  await user.click(screen.getByRole('option', { name: /Old · old/ }))
  await waitFor(() => expect(mocks.diffSnapshot).toHaveBeenCalledTimes(1))
  expect(mocks.diffSnapshot.mock.calls[0][2]).toBe('old')
  expect(mocks.diffSnapshot.mock.calls[0][3]).toBe('new')

  expect(await screen.findByText('selected comparison')).toBeTruthy()
  expect(screen.getByRole('dialog').className).toContain('h-[min(90dvh,760px)]')
  await act(async () =>
    pending[0]({
      diff: 'stale comparison',
      missingBase: false,
      missingTarget: false,
    }),
  )
  expect(screen.queryByText('stale comparison')).toBeNull()
})

it('shows the latest current content diff without another click', async () => {
  mocks.listSnapshots.mockResolvedValue([{ oid: 'latest', message: 'Latest' }])
  mocks.diffSnapshotWithCurrent.mockResolvedValue({
    diff: 'current comparison',
    missingBase: false,
    missingTarget: false,
  })
  render(
    <SnapshotComparisonDialog
      onClose={vi.fn()}
      uri="viking://resources/note.md"
    />,
  )
  expect(await screen.findByText('current comparison')).toBeTruthy()
  expect(
    screen.getByRole('combobox', { name: 'previousVersion' }).textContent,
  ).toContain('Latest')
  expect(
    screen.getByRole('combobox', { name: 'selectedVersion' }).textContent,
  ).toContain('currentContent')
})
