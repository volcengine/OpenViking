// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { TasksRoute } from './route'

const apiMocks = vi.hoisted(() => ({
  cancelTask: vi.fn(),
  fetchTasks: vi.fn(),
}))

const toastMocks = vi.hoisted(() => ({
  dismiss: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  loading: vi.fn(() => 'toast-1'),
  success: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { language: 'en', resolvedLanguage: 'en' },
    t: (key: string) => key,
  }),
}))

vi.mock('sonner', () => ({ toast: toastMocks }))

vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'default/default' }),
}))

vi.mock('#/routes/monitoring/-components/queue-status-card', () => ({
  QueueStatusCard: () => <div data-testid="mock-queue-status-card" />,
}))

vi.mock('./-components/task-detail-sheet', () => ({
  TaskDetailSheet: () => null,
}))

vi.mock('#/lib/sessions/api', () => ({
  commitSession: vi.fn(),
}))

vi.mock('#/gen/ov-client', () => ({
  postResources: vi.fn(),
}))

vi.mock('#/lib/ov-client', () => ({
  ovClient: { instance: { post: vi.fn() } },
}))

vi.mock('./-lib/task-list', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    cancelTask: apiMocks.cancelTask,
    fetchTasks: apiMocks.fetchTasks,
  }
})

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  })
  const result = render(
    <QueryClientProvider client={queryClient}>
      <TasksRoute />
    </QueryClientProvider>,
  )
  return { ...result, queryClient }
}

const runningTask = {
  created_at: 2,
  status: 'running',
  task_id: 'task-running',
  task_type: 'session_commit',
}

const pendingTask = {
  created_at: 1,
  status: 'pending',
  task_id: 'task-pending',
  task_type: 'add_resource',
}

const completedTask = {
  created_at: 0,
  status: 'completed',
  task_id: 'task-completed',
  task_type: 'session_commit',
}

beforeEach(() => {
  apiMocks.fetchTasks.mockResolvedValue([
    runningTask,
    pendingTask,
    completedTask,
  ])
  apiMocks.cancelTask.mockResolvedValue({
    task_id: 'task-running',
    status: 'cancelling',
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('TasksRoute cancel action', () => {
  it('shows the cancel action for pending and running cancellable tasks', async () => {
    renderPage()

    const cancelButtons = await screen.findAllByRole('button', {
      name: 'actions.cancelTask',
    })
    expect(cancelButtons).toHaveLength(2)
  })

  it('hides the cancel action for terminal statuses and non-cancellable types', async () => {
    apiMocks.fetchTasks.mockResolvedValue([
      completedTask,
      {
        created_at: 3,
        status: 'running',
        task_id: 'task-skill',
        task_type: 'add_skill',
      },
    ])

    renderPage()

    await screen.findByText('task-skill')
    expect(
      screen.queryByRole('button', { name: 'actions.cancelTask' }),
    ).toBeNull()
  })

  it('shows the cancelling intermediate state without an active button', async () => {
    apiMocks.fetchTasks.mockResolvedValue([
      {
        created_at: 2,
        status: 'cancelling',
        task_id: 'task-cancelling',
        task_type: 'session_commit',
      },
    ])

    renderPage()

    expect(await screen.findByTitle('status.cancelling')).toBeDefined()
    expect(
      screen.queryByRole('button', { name: 'actions.cancelTask' }),
    ).toBeNull()
  })

  it('asks for confirmation and calls the cancel endpoint', async () => {
    renderPage()

    const cancelButton = await screen.findAllByRole('button', {
      name: 'actions.cancelTask',
    })
    fireEvent.click(cancelButton[0])

    expect(await screen.findByText('cancelDialog.title')).toBeDefined()
    expect(screen.getByText('cancelDialog.description')).toBeDefined()
    expect(apiMocks.cancelTask).not.toHaveBeenCalled()

    fireEvent.click(
      screen.getByRole('button', { name: 'cancelDialog.confirm' }),
    )

    await waitFor(() => {
      expect(apiMocks.cancelTask).toHaveBeenCalledWith('task-running')
    })
    await waitFor(() => {
      expect(toastMocks.success).toHaveBeenCalled()
    })
    await waitFor(() => {
      expect(screen.queryByText('cancelDialog.title')).toBeNull()
    })
  })

  it('keeps the dialog open and reports failures', async () => {
    apiMocks.cancelTask.mockRejectedValue(new Error('cancel rejected'))

    renderPage()

    const cancelButton = await screen.findAllByRole('button', {
      name: 'actions.cancelTask',
    })
    fireEvent.click(cancelButton[0])

    fireEvent.click(
      await screen.findByRole('button', { name: 'cancelDialog.confirm' }),
    )

    await waitFor(() => {
      expect(toastMocks.error).toHaveBeenCalledWith('cancel rejected')
    })
    expect(await screen.findByText('cancelDialog.title')).toBeDefined()
  })

  it('dismisses the dialog without cancelling', async () => {
    renderPage()

    const cancelButton = await screen.findAllByRole('button', {
      name: 'actions.cancelTask',
    })
    fireEvent.click(cancelButton[0])

    fireEvent.click(
      await screen.findByRole('button', { name: 'cancelDialog.dismiss' }),
    )

    await waitFor(() => {
      expect(screen.queryByText('cancelDialog.title')).toBeNull()
    })
    expect(apiMocks.cancelTask).not.toHaveBeenCalled()
  })
})
