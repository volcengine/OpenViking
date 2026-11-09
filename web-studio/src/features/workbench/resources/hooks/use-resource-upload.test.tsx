// @vitest-environment jsdom

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { toast } from 'sonner'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ResourceUploadProvider,
  useResourceUpload,
} from '#/features/workbench/resources/hooks/use-resource-upload'
import i18n from '#/i18n'
import type * as OvClientModule from '#/shared/api/ov-client/index'

const apiMocks = vi.hoisted(() => ({
  getTasks: vi.fn(),
  postResources: vi.fn(),
}))

vi.mock('#/shared/api/ov-client/index', async (importOriginal) => ({
  ...(await importOriginal<typeof OvClientModule>()),
  getOvResult: async (value: unknown) => value,
  getTasks: apiMocks.getTasks,
  isOvClientError: () => false,
  postResources: apiMocks.postResources,
  postResourcesTempUpload: vi.fn(),
}))

vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

it('does not load upload tasks when the workbench only supports reads', async () => {
  render(
    <ResourceUploadProvider enabled={false}>
      <span data-testid="read-only" />
    </ResourceUploadProvider>,
  )
  await screen.findByTestId('read-only')
  expect(apiMocks.getTasks).not.toHaveBeenCalled()
})

function UploadHarness({
  onAccepted,
  onCompleted,
  onFailed,
}: {
  onAccepted: (result: {
    rootUri: string | null
    taskId: string | null
  }) => void
  onCompleted: () => void
  onFailed?: () => void
}) {
  const { refreshTasks, startRemote, tasks } = useResourceUpload()

  return (
    <>
      <button
        type="button"
        data-testid="start"
        onClick={() =>
          startRemote({
            commonBody: { watch_interval: 60 },
            onAccepted,
            onCompleted,
            onFailed,
            url: 'https://github.com/volcengine/OpenViking',
          })
        }
      />
      <button
        type="button"
        data-testid="refresh"
        onClick={() => void refreshTasks()}
      />
      <output data-testid="task-error">{tasks[0]?.errorMessage}</output>
    </>
  )
}

describe('ResourceUploadProvider remote completion', () => {
  it.each(['resolve', 'reject'] as const)(
    'stops remote notifications after unmount when the request later %ss',
    async (outcome) => {
      let resolveImport!: (result: { status: string; root_uri: string }) => void
      let rejectImport!: (error: Error) => void
      apiMocks.getTasks.mockResolvedValue([])
      apiMocks.postResources.mockImplementation(
        () =>
          new Promise((resolve, reject) => {
            resolveImport = resolve
            rejectImport = reject
          }),
      )
      const onAccepted = vi.fn()
      const onCompleted = vi.fn()
      const onFailed = vi.fn()
      const { unmount } = render(
        <ResourceUploadProvider>
          <UploadHarness
            onAccepted={onAccepted}
            onCompleted={onCompleted}
            onFailed={onFailed}
          />
        </ResourceUploadProvider>,
      )

      fireEvent.click(screen.getByTestId('start'))
      await waitFor(() => expect(apiMocks.postResources).toHaveBeenCalledOnce())
      const signal = apiMocks.postResources.mock.calls[0][0]
        .signal as AbortSignal
      expect(signal.aborted).toBe(false)
      unmount()
      expect(signal.aborted).toBe(true)

      await act(async () => {
        if (outcome === 'resolve') {
          resolveImport({
            status: 'success',
            root_uri: 'viking://resources/OpenViking',
          })
        } else {
          rejectImport(new Error('Late import failure'))
        }
      })

      expect(onAccepted).not.toHaveBeenCalled()
      expect(onCompleted).not.toHaveBeenCalled()
      expect(onFailed).not.toHaveBeenCalled()
      expect(toast.success).not.toHaveBeenCalled()
      expect(toast.error).not.toHaveBeenCalled()
      expect(apiMocks.getTasks).toHaveBeenCalledOnce()
    },
  )

  it('notifies after submission and again after the background task completes', async () => {
    let taskStatus = 'processing'
    apiMocks.getTasks.mockImplementation(() => [
      {
        created_at: 1,
        result: { root_uri: 'viking://resources/OpenViking' },
        status: taskStatus,
        task_id: 'resource-task',
        task_type: 'add_resource',
        updated_at: 1,
      },
    ])
    apiMocks.postResources.mockResolvedValue({
      root_uri: 'viking://resources/OpenViking',
      status: 'success',
      task_id: 'resource-task',
    })
    const onAccepted = vi.fn()
    const onCompleted = vi.fn()

    render(
      <ResourceUploadProvider>
        <UploadHarness onAccepted={onAccepted} onCompleted={onCompleted} />
      </ResourceUploadProvider>,
    )

    fireEvent.click(screen.getByTestId('start'))
    await waitFor(() => expect(apiMocks.postResources).toHaveBeenCalledOnce())
    await waitFor(() =>
      expect(apiMocks.getTasks.mock.calls.length).toBeGreaterThan(1),
    )
    expect(onAccepted).toHaveBeenCalledWith({
      rootUri: 'viking://resources/OpenViking',
      taskId: 'resource-task',
    })
    expect(onCompleted).not.toHaveBeenCalled()

    taskStatus = 'completed'
    fireEvent.click(screen.getByTestId('refresh'))

    await waitFor(() => expect(onCompleted).toHaveBeenCalledOnce())
  })

  it('notifies when the background task fails', async () => {
    apiMocks.getTasks.mockResolvedValue([
      {
        created_at: 1,
        error: 'Processing failed',
        status: 'failed',
        task_id: 'resource-task',
        task_type: 'add_resource',
        updated_at: 2,
      },
    ])
    apiMocks.postResources.mockResolvedValue({
      root_uri: 'viking://resources/OpenViking',
      status: 'success',
      task_id: 'resource-task',
    })
    const onFailed = vi.fn()

    render(
      <ResourceUploadProvider>
        <UploadHarness
          onAccepted={vi.fn()}
          onCompleted={vi.fn()}
          onFailed={onFailed}
        />
      </ResourceUploadProvider>,
    )

    fireEvent.click(screen.getByTestId('start'))

    await waitFor(() => expect(onFailed).toHaveBeenCalledOnce())
  })

  it('updates an empty server error fallback when the language changes', async () => {
    const previousLanguage = i18n.language
    try {
      await i18n.changeLanguage('en')
      apiMocks.getTasks.mockResolvedValue([
        {
          created_at: 1,
          status: 'failed',
          task_id: 'resource-task',
          task_type: 'add_resource',
          updated_at: 2,
        },
      ])

      render(
        <ResourceUploadProvider>
          <UploadHarness onAccepted={vi.fn()} onCompleted={vi.fn()} />
        </ResourceUploadProvider>,
      )

      await waitFor(() =>
        expect(screen.getByTestId('task-error').textContent).toBe(
          'Processing failed',
        ),
      )

      await i18n.changeLanguage('zh-CN')

      await waitFor(() =>
        expect(screen.getByTestId('task-error').textContent).toBe('处理失败'),
      )
    } finally {
      await i18n.changeLanguage(previousLanguage)
    }
  })
})
