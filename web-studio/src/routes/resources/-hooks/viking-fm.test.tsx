// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchFileContent } from '../-lib/api'
import type { VikingFsEntry } from '../-types/viking-fm'
import { useVikingFilePreview } from './viking-fm'

vi.mock('../-lib/api', () => {
  return {
    fetchFileContent: vi.fn(),
    fetchFind: vi.fn(),
    fetchFindAllTypes: vi.fn(),
    fetchFsList: vi.fn(),
    fetchFsStat: vi.fn(),
    fetchFsTree: vi.fn(),
  }
})

const largeJson: VikingFsEntry = {
  uri: 'viking://resources/large.json',
  name: 'large.json',
  isDir: false,
  size: '3 MiB',
  sizeBytes: 3 * 1024 * 1024,
  modTime: '2026-07-31 10:00',
  modTimestamp: null,
  abstract: '',
  overview: '',
}

describe('useVikingFilePreview', () => {
  beforeEach(() => {
    vi.mocked(fetchFileContent).mockReset()
  })

  it('exposes explicitly loaded content for a large file', async () => {
    vi.mocked(fetchFileContent).mockResolvedValue({
      uri: largeJson.uri,
      content: '{"loaded":true}',
      offset: 0,
      limit: -1,
      truncated: false,
    })
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const wrapper = ({ children }: PropsWithChildren) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(
      () =>
        useVikingFilePreview(
          largeJson,
          {
            maxAutoReadBytes: 2 * 1024 * 1024,
            defaultReadLimit: -1,
            requireKnownSize: true,
          },
          { raw: true },
        ),
      { wrapper },
    )

    expect(result.current.preview?.shouldAutoRead).toBe(false)
    expect(result.current.canLoadContent).toBe(true)
    expect(fetchFileContent).not.toHaveBeenCalled()

    await act(async () => {
      await result.current.refetch()
    })

    await waitFor(() => {
      expect(result.current.isContentLoaded).toBe(true)
    })
    expect(result.current.preview?.content).toBe('{"loaded":true}')
    expect(fetchFileContent).toHaveBeenCalledOnce()
  })
})
