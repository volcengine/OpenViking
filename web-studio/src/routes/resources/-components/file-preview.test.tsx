// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { describe, expect, it, vi } from 'vitest'

import type { VikingFsEntry } from '../-types/viking-fm'
import { FilePreview } from './file-preview'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('#/gen/ov-client/client.gen', () => ({
  client: {
    buildUrl: ({ query }: { query: { uri: string } }) =>
      `/api/v1/content/download?uri=${encodeURIComponent(query.uri)}`,
  },
}))

vi.mock('#/lib/ov-client', () => ({
  getContentDownload: vi.fn(),
  ovClient: { getOptions: () => ({ baseUrl: '' }) },
}))

vi.mock('../-hooks/viking-fm', () => ({
  useInvalidateVikingFs: () => ({
    invalidateList: vi.fn(),
    invalidatePreview: vi.fn(),
    invalidateTree: vi.fn(),
  }),
  useVikingFilePreview: () => ({
    canLoadContent: false,
    isContentLoaded: true,
    isFetching: false,
    isLoading: false,
    preview: {
      content: '[Target](./target.md)',
      fileType: 'markdown',
      shouldAutoRead: true,
    },
    refetch: vi.fn(),
  }),
  useVikingFsStat: () => ({
    data: undefined,
    isLoading: false,
  }),
}))

const file: VikingFsEntry = {
  abstract: '',
  isDir: false,
  modTime: '2026-08-04 12:00',
  modTimestamp: null,
  name: 'index.md',
  overview: '',
  size: '24 B',
  sizeBytes: 24,
  uri: 'viking://resources/wiki/index.md',
}

describe('FilePreview Markdown links', () => {
  it('opens internal Markdown links in the resource preview', () => {
    const onNavigate = vi.fn()
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const wrapper = ({ children }: PropsWithChildren) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )

    render(
      <FilePreview
        file={file}
        onClose={vi.fn()}
        onNavigate={onNavigate}
        showCloseButton={false}
      />,
      { wrapper },
    )

    const link = screen.getByRole('link', { name: 'Target' })
    expect(link.getAttribute('href')).toBe('viking://resources/wiki/target.md')

    fireEvent.click(link)

    expect(onNavigate).toHaveBeenCalledOnce()
    expect(onNavigate).toHaveBeenCalledWith('viking://resources/wiki/target.md')
  })
})
