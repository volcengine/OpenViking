// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AddResourceForm } from './add-resource-page'

const uploadMocks = vi.hoisted(() => ({
  enqueueUploads: vi.fn(),
  resetRemote: vi.fn(),
  startRemote: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../-hooks/use-resource-upload', () => ({
  useResourceUpload: () => ({
    ...uploadMocks,
    remoteState: {
      error: null,
      phase: 'idle',
      remoteUrl: '',
      skippedFiles: [],
      taskId: null,
    },
  }),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('AddResourceForm watch options', () => {
  it('submits watch_interval for a watched remote resource', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <AddResourceForm initialMode="remote" initialWatchEnabled />
      </QueryClientProvider>,
    )

    fireEvent.change(screen.getByRole('textbox', { name: 'remoteUrl' }), {
      target: { value: 'https://github.com/volcengine/OpenViking' },
    })
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'watch.interval' }),
      { target: { value: '60' } },
    )
    fireEvent.click(screen.getByRole('button', { name: 'startProcessing' }))

    expect(uploadMocks.startRemote).toHaveBeenCalledWith({
      commonBody: expect.objectContaining({
        parent: 'viking://resources/',
        watch_interval: 60,
      }),
      onAccepted: undefined,
      onCompleted: undefined,
      onFailed: undefined,
      url: 'https://github.com/volcengine/OpenViking',
    })
  })
})
