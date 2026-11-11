// @vitest-environment jsdom
import { cleanup, render, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type {
  FilePreviewProps,
  FilePreviewFileTab,
} from '@openviking/react/components/file-preview'
import { afterEach, expect, it, vi } from 'vitest'
import { SharedFilePreview } from './shared-file-preview'

const mocks = vi.hoisted(() => ({
  preview: vi.fn<(props: FilePreviewProps) => null>(() => null),
  read: vi.fn(),
  directory: vi.fn(),
  download: vi.fn(),
  save: vi.fn(),
  invalidate: vi.fn(),
}))
vi.mock('@openviking/react/components/file-preview', () => ({
  FilePreview: mocks.preview,
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'user-1' }),
}))
vi.mock('#/features/workbench/resources/lib/api', () => ({
  fetchFileContent: mocks.read,
  fetchDirectoryLevelContent: mocks.directory,
  saveFileContent: mocks.save,
}))
vi.mock('#/features/workbench/resources/hooks/viking-fm', () => ({
  useInvalidateVikingFs: () => ({ invalidatePreview: mocks.invalidate }),
}))
vi.mock('#/shared/api/ov-client', () => ({
  getContentDownload: mocks.download,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'zh-CN' },
  }),
}))
const file = {
  uri: 'viking://user/default/memories/profile.md',
  name: 'profile.md',
  isDir: false,
  size: '',
  sizeBytes: 50,
  modTime: '',
  modTimestamp: null,
  abstract: '',
}
function setup(overrides = {}) {
  const props = {
    file,
    onNavigate: vi.fn(),
    onDirtyChange: vi.fn(),
    onSaved: vi.fn(),
    ...overrides,
  }
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <SharedFilePreview {...props} />
    </QueryClientProvider>,
  )
  return props
}
function latest() {
  return mocks.preview.mock.calls.at(-1)?.[0] as unknown as FilePreviewProps
}
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})
it('loads truncated previews but edits and saves full content, and clears the navigation guard', async () => {
  mocks.read.mockResolvedValue({ content: '# Preview', truncated: true })
  mocks.download.mockResolvedValue({ data: '# Full content' })
  const props = setup({ defaultReadLimit: 10000 })
  await waitFor(() =>
    expect((latest().tabs[0] as FilePreviewFileTab).editing).toBeTruthy(),
  )
  const tab = latest().tabs[0] as FilePreviewFileTab
  expect(tab.source.truncated).toBe(true)
  expect(mocks.read).toHaveBeenCalledWith(file.uri, { limit: 10000, raw: true })
  await act(async () => {
    expect(await tab.editing!.loadContent!(tab.source)).toBe('# Full content')
  })
  expect(props.onDirtyChange).toHaveBeenLastCalledWith(true)
  await tab.editing!.save('# Updated')
  expect(mocks.save).toHaveBeenCalledWith(file.uri, '# Updated')
  act(() => tab.editing!.onSave!('# Updated'))
  expect(mocks.invalidate).toHaveBeenCalledWith(file.uri)
  expect(props.onSaved).toHaveBeenCalledWith(file.uri)
  expect(props.onDirtyChange).toHaveBeenLastCalledWith(false)
  act(() => tab.editing!.onCancel!('draft'))
  expect(props.onDirtyChange).toHaveBeenLastCalledWith(false)
})
it('keeps special directory content available when summaries fail', async () => {
  mocks.directory.mockRejectedValue(new Error('summary unavailable'))
  setup({
    file: { ...file, isDir: true },
    content: <div>Directory entries</div>,
  })
  await waitFor(() => expect(latest().tabs[1].kind).toBe('content'))
  expect(latest().tabs[0].kind).toBe('content')
})
it('downloads image previews through the authenticated client and disables editing', async () => {
  mocks.download.mockResolvedValue({ data: new Blob(['image']) })
  setup({
    file: { ...file, uri: 'viking://resources/image.png', name: 'image.png' },
  })
  await waitFor(() =>
    expect((latest().tabs[0] as FilePreviewFileTab).source.file).toBeInstanceOf(
      Blob,
    ),
  )
  expect((latest().tabs[0] as FilePreviewFileTab).editing).toBeUndefined()
  expect(mocks.download).toHaveBeenCalledWith(
    expect.objectContaining({ responseType: 'blob', throwOnError: true }),
  )
})
