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

function UploadHarness({ onCompleted }: { onCompleted: () => void }) {
  const { refreshTasks, startRemote } = useResourceUpload()

  return (
    <>
      <button
        type="button"
        data-testid="start"
        onClick={() =>
          startRemote({
            commonBody: { watch_interval: 60 },
            onCompleted,
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
  it('waits for the background resource task before notifying completion', async () => {
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
    const onCompleted = vi.fn()

    render(
      <ResourceUploadProvider>
        <UploadHarness onCompleted={onCompleted} />
      </ResourceUploadProvider>,
    )

    fireEvent.click(screen.getByTestId('start'))
    await waitFor(() => expect(apiMocks.postResources).toHaveBeenCalledOnce())
    await waitFor(() =>
      expect(apiMocks.getTasks.mock.calls.length).toBeGreaterThan(1),
    )
    expect(onCompleted).not.toHaveBeenCalled()

    taskStatus = 'completed'
    fireEvent.click(screen.getByTestId('refresh'))

    await waitFor(() => expect(onCompleted).toHaveBeenCalledOnce())
  })
})
