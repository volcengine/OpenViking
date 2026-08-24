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

const directory: VikingFsEntry = {
  ...file,
  isDir: true,
  name: 'wiki',
  overview: '',
  uri: 'viking://resources/wiki',
}

function renderPreview(
  entry: VikingFsEntry,
  onNavigate: (uri: string) => void,
  directoryOverview?: string,
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  if (entry.isDir) {
    queryClient.setQueryData(
      ['viking-directory-level', entry.uri, 'abstract'],
      '',
    )
    queryClient.setQueryData(
      ['viking-directory-level', entry.uri, 'overview'],
      directoryOverview,
    )
  }
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )

  return render(
    <FilePreview
      file={entry}
      onClose={vi.fn()}
      onNavigate={onNavigate}
      showCloseButton={false}
    />,
    { wrapper },
  )
}

describe('FilePreview Markdown links', () => {
  it('opens internal Markdown links in the resource preview', () => {
    const onNavigate = vi.fn()
    renderPreview(file, onNavigate)

    const link = screen.getByRole('link', { name: 'Target' })
    expect(link.getAttribute('href')).toBe('viking://resources/wiki/target.md')

    fireEvent.click(link)

    expect(onNavigate).toHaveBeenCalledOnce()
    expect(onNavigate).toHaveBeenCalledWith('viking://resources/wiki/target.md')
  })

  it('opens viking links from a directory overview', () => {
    const onNavigate = vi.fn()
    renderPreview(
      directory,
      onNavigate,
      [
        '[Target file](viking://resources/wiki/target.md)',
        '[Target directory](viking://resources/wiki/target-directory)',
      ].join('\n\n'),
    )

    const fileLink = screen.getByRole('link', { name: 'Target file' })
    const directoryLink = screen.getByRole('link', {
      name: 'Target directory',
    })
    expect(fileLink.getAttribute('href')).toBe(
      'viking://resources/wiki/target.md',
    )
    expect(directoryLink.getAttribute('href')).toBe(
      'viking://resources/wiki/target-directory',
    )

    fireEvent.click(fileLink)
    fireEvent.click(directoryLink)

    expect(onNavigate).toHaveBeenNthCalledWith(
      1,
      'viking://resources/wiki/target.md',
    )
    expect(onNavigate).toHaveBeenNthCalledWith(
      2,
      'viking://resources/wiki/target-directory',
    )
  })

  it('decodes encoded viking links before navigating', () => {
    const onNavigate = vi.fn()
    renderPreview(
      directory,
      onNavigate,
      '[目标](viking://resources/%E8%B5%84%E6%96%99/%E7%9B%AE%E6%A0%87.md)',
    )

    fireEvent.click(screen.getByRole('link', { name: '目标' }))

    expect(onNavigate).toHaveBeenCalledWith('viking://resources/资料/目标.md')
  })

  it('preserves non-viking links in a directory overview', () => {
    const onNavigate = vi.fn()
    renderPreview(
      directory,
      onNavigate,
      [
        '[Relative](child.md)',
        '[Protocol relative](//example.com/child.md)',
        '[External](https://example.com/child.md)',
        '[Data](data:text/html,unsafe)',
        '[Blob](blob:https://example.com/id)',
      ].join('\n\n'),
    )

    expect(
      screen.getByRole('link', { name: 'Relative' }).getAttribute('href'),
    ).toBe('child.md')
    expect(
      screen
        .getByRole('link', { name: 'Protocol relative' })
        .getAttribute('href'),
    ).toBe('//example.com/child.md')
    const externalLink = screen.getByRole('link', { name: 'External' })
    expect(externalLink.getAttribute('href')).toBe(
      'https://example.com/child.md',
    )
    expect(externalLink.getAttribute('target')).toBeNull()
    expect(screen.queryByRole('link', { name: 'Data' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Blob' })).toBeNull()
    expect(onNavigate).not.toHaveBeenCalled()
  })
})
