// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ResourceUploadProvider,
  useResourceUpload,
} from './use-resource-upload'

const apiMocks = vi.hoisted(() => ({
  getTasks: vi.fn(),
  postResources: vi.fn(),
}))

vi.mock('#/lib/ov-client', () => ({
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
  const { refreshTasks, startRemote } = useResourceUpload()

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
    </>
  )
}

describe('ResourceUploadProvider remote completion', () => {
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
})
