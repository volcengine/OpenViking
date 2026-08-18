// @vitest-environment jsdom

import * as React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  act,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { WatchManagementPage } from './route'
import type { WatchTask } from './-lib/api'

const apiMocks = vi.hoisted(() => ({
  deleteWatch: vi.fn(),
  fetchWatchProcessingHistory: vi.fn(),
  fetchWatches: vi.fn(),
  triggerWatch: vi.fn(),
  updateWatch: vi.fn(),
}))

const toastMocks = vi.hoisted(() => ({
  dismiss: vi.fn(),
  error: vi.fn(),
  loading: vi.fn(() => 'toast-1'),
  success: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { resolvedLanguage: 'en' },
    t: (key: string) => key,
  }),
}))

vi.mock('sonner', () => ({ toast: toastMocks }))

vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'default/default' }),
}))

vi.mock('#/routes/resources/resource-upload', () => ({
  AddResourceForm: (props: {
    onAccepted?: (result: { rootUri: string | null }) => void
    onCompleted?: () => void
    onFailed?: () => void
    onSubmitted?: () => void
  }) => (
    <div>
      <button
        type="button"
        data-testid="mock-submit"
        onClick={() => {
          props.onSubmitted?.()
          props.onAccepted?.({ rootUri: watch.toUri })
        }}
      />
      <button
        type="button"
        data-testid="mock-fail"
        onClick={() => {
          props.onSubmitted?.()
          props.onFailed?.()
        }}
      />
    </div>
  ),
  ResourceUploadProvider: ({ children }: { children: React.ReactNode }) =>
    children,
}))

vi.mock('./-components/edit-watch-dialog', () => ({
  EditWatchDialog: () => null,
}))

vi.mock('./-components/watch-history-sheet', () => ({
  WatchHistorySheet: () => null,
}))

vi.mock('./-lib/api', () => apiMocks)

const watch: WatchTask = {
  createdAt: '2026-08-10T00:00:00Z',
  instruction: '',
  isActive: true,
  lastExecutionTime: null,
  nextExecutionTime: '2026-08-11T00:00:00Z',
  path: 'https://github.com/volcengine/OpenViking',
  reason: '',
  taskId: 'watch-1',
  toUri: 'viking://resources/OpenViking',
  watchInterval: 1440,
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  })
  const result = render(
    <QueryClientProvider client={queryClient}>
      <WatchManagementPage />
    </QueryClientProvider>,
  )
  return { ...result, queryClient }
}

beforeEach(() => {
  apiMocks.fetchWatches.mockResolvedValue([watch])
  apiMocks.fetchWatchProcessingHistory.mockResolvedValue([
    { status: 'running', task_id: 'processing-1' },
  ])
  apiMocks.triggerWatch.mockResolvedValue(undefined)
  apiMocks.updateWatch.mockResolvedValue(undefined)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.useRealTimers()
})

describe('WatchManagementPage', () => {
  it('shows edit as a direct row action', async () => {
    renderPage()

    expect(
      await screen.findByRole('button', { name: 'actions.edit' }),
    ).toBeDefined()
  })

  it('keeps Sync now disabled and labelled as syncing while processing is active', async () => {
    const existingTask = { status: 'completed', task_id: 'existing-task' }
    const runningTask = { status: 'running', task_id: 'triggered-task' }
    const completedTask = { status: 'completed', task_id: 'triggered-task' }
    apiMocks.fetchWatchProcessingHistory
      .mockResolvedValueOnce([existingTask])
      .mockResolvedValue([runningTask, existingTask])
    const { queryClient } = renderPage()

    fireEvent.click(
      await screen.findByRole('button', { name: 'actions.trigger' }),
    )

    const syncingButton = await screen.findByRole('button', {
      name: 'actions.syncing',
    })
    expect((syncingButton as HTMLButtonElement).disabled).toBe(true)
    expect(apiMocks.triggerWatch).toHaveBeenCalledWith(watch.taskId)

    apiMocks.fetchWatchProcessingHistory.mockResolvedValue([
      completedTask,
      existingTask,
    ])
    await queryClient.invalidateQueries({
      queryKey: ['watch-sync-progress', 'default/default'],
    })

    expect(
      await screen.findByRole('button', { name: 'actions.trigger' }),
    ).toBeDefined()
  })

  it('falls back to the watch execution time when the task baseline fails', async () => {
    apiMocks.fetchWatchProcessingHistory.mockRejectedValueOnce(
      new Error('history unavailable'),
    )
    const { queryClient } = renderPage()

    fireEvent.click(
      await screen.findByRole('button', { name: 'actions.trigger' }),
    )

    expect(
      await screen.findByRole('button', { name: 'actions.syncing' }),
    ).toBeDefined()
    expect(apiMocks.triggerWatch).toHaveBeenCalledWith(watch.taskId)

    apiMocks.fetchWatches.mockResolvedValue([
      { ...watch, lastExecutionTime: '2026-08-10T01:00:00Z' },
    ])
    await queryClient.invalidateQueries({
      queryKey: ['watches', 'default/default'],
    })

    expect(
      await screen.findByRole('button', { name: 'actions.trigger' }),
    ).toBeDefined()
  })

  it('updates the enabled state and reports mutation failures', async () => {
    apiMocks.updateWatch.mockRejectedValueOnce(new Error('update failed'))
    renderPage()

    fireEvent.click(
      await screen.findByRole('switch', { name: 'actions.disable' }),
    )

    await waitFor(() =>
      expect(apiMocks.updateWatch).toHaveBeenCalledWith(watch.taskId, {
        isActive: false,
      }),
    )
    await waitFor(() =>
      expect(toastMocks.error).toHaveBeenCalledWith('update failed'),
    )
  })

  it('disables manual sync when the scheduled sync is disabled', async () => {
    apiMocks.fetchWatches.mockResolvedValue([{ ...watch, isActive: false }])
    renderPage()

    const statusSwitch = await screen.findByRole('switch', {
      name: 'actions.enable',
    })
    const syncButton = screen.getByRole('button', { name: 'actions.trigger' })

    expect(
      (statusSwitch as HTMLButtonElement).getAttribute('aria-checked'),
    ).toBe('false')
    expect((syncButton as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText('status.disabled')).toBeDefined()
  })

  it('enters creation state after submitting a new watch', async () => {
    apiMocks.fetchWatches.mockResolvedValue([])
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: 'add' }))
    fireEvent.click(screen.getByTestId('mock-submit'))

    expect(screen.getByRole('status').textContent).toContain('creation.title')
    expect(toastMocks.loading).toHaveBeenCalledWith('toast.creating')
  })

  it('clears creation state when resource submission fails', async () => {
    renderPage()

    fireEvent.click(await screen.findByRole('button', { name: 'add' }))
    fireEvent.click(screen.getByTestId('mock-fail'))

    expect(screen.queryByRole('status')).toBeNull()
    expect(toastMocks.dismiss).toHaveBeenCalledWith('toast-1')
  })

  it('warns and clears creation state when discovery times out', async () => {
    vi.useFakeTimers()
    apiMocks.fetchWatches.mockResolvedValue([])
    renderPage()

    await vi.advanceTimersByTimeAsync(0)
    fireEvent.click(screen.getByRole('button', { name: 'add' }))
    fireEvent.click(screen.getByTestId('mock-submit'))
    expect(screen.getByRole('status')).toBeDefined()
    await act(async () => {})
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })

    expect(screen.queryByRole('status')).toBeNull()
    expect(toastMocks.warning).toHaveBeenCalledWith('toast.createTimeout', {
      id: 'toast-1',
    })
  })
})
