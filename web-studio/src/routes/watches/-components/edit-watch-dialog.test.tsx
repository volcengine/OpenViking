// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { EditWatchDialog, getWatchUpdateInput } from './edit-watch-dialog'
import type { WatchTask } from '../-lib/api'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

const watch: WatchTask = {
  createdAt: '2026-08-10T00:00:00Z',
  instruction: 'Keep headings',
  isActive: true,
  lastExecutionTime: null,
  nextExecutionTime: '2026-08-11T00:00:00Z',
  path: 'https://github.com/volcengine/OpenViking',
  reason: 'Keep docs current',
  taskId: 'watch-1',
  toUri: 'viking://resources/OpenViking',
  watchInterval: 1440,
}

afterEach(cleanup)

describe('EditWatchDialog', () => {
  it('submits changed interval, reason, and instruction fields', () => {
    const onSubmit = vi.fn()
    render(
      <EditWatchDialog
        watch={watch}
        pending={false}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    )

    expect(
      screen.getByPlaceholderText('editDialog.reasonPlaceholder'),
    ).toBeTruthy()
    expect(
      screen.getByPlaceholderText('editDialog.instructionPlaceholder'),
    ).toBeTruthy()

    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'editDialog.interval' }),
      { target: { value: '60' } },
    )
    fireEvent.change(
      screen.getByRole('textbox', { name: 'editDialog.reason' }),
      { target: { value: 'Track upstream releases' } },
    )
    fireEvent.change(
      screen.getByRole('textbox', { name: 'editDialog.instruction' }),
      { target: { value: 'Preserve code examples' } },
    )
    fireEvent.click(screen.getByRole('button', { name: 'save' }))

    expect(onSubmit).toHaveBeenCalledWith({
      instruction: 'Preserve code examples',
      reason: 'Track upstream releases',
      watchInterval: 60,
    })
  })

  it('keeps unchanged fields out of a partial update', () => {
    expect(
      getWatchUpdateInput(
        watch,
        watch.watchInterval,
        'New reason',
        watch.instruction,
      ),
    ).toEqual({ reason: 'New reason' })
  })

  it('defers interval range validation to the server', () => {
    const onSubmit = vi.fn()
    render(
      <EditWatchDialog
        watch={watch}
        pending={false}
        onOpenChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    )

    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'editDialog.interval' }),
      { target: { value: '-1' } },
    )
    fireEvent.click(screen.getByRole('button', { name: 'save' }))

    expect(onSubmit).toHaveBeenCalledWith({ watchInterval: -1 })
  })
})
