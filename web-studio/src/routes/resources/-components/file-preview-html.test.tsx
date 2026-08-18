// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
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

const markdown = [
  '# Heading',
  '',
  '<div>rendered</div>',
  '',
  '<table><tbody><tr><th colspan="2">head</th></tr><tr><td rowspan="2">cell</td><td>a</td></tr><tr><td>b</td></tr></tbody></table>',
  '',
  '<script>window.__pwned = true</script>',
].join('\n')

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
      content: markdown,
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

function renderPreview() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return render(
    <FilePreview
      file={file}
      onClose={vi.fn()}
      onNavigate={vi.fn()}
      showCloseButton={false}
    />,
    { wrapper },
  )
}

describe('FilePreview Markdown raw HTML', () => {
  it('renders raw HTML elements instead of escaping them', () => {
    const { container } = renderPreview()

    const rendered = Array.from(container.querySelectorAll('div')).find(
      (el) => el.textContent === 'rendered',
    )
    expect(rendered).toBeTruthy()
    expect(container.textContent).not.toContain('<div>')
  })

  it('renders raw HTML tables through the shared table components', () => {
    const { container } = renderPreview()

    const head = container.querySelector('table th')
    const spanned = container.querySelector('table td')
    expect(head?.textContent).toBe('head')
    expect(head?.getAttribute('colspan')).toBe('2')
    expect(spanned?.textContent).toBe('cell')
    expect(spanned?.getAttribute('rowspan')).toBe('2')
  })

  it('strips script tags', () => {
    const { container } = renderPreview()

    expect(container.querySelector('script')).toBeNull()
  })
})
