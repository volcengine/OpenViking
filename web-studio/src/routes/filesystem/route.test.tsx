// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ComponentType, PropsWithChildren } from 'react'
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest'

import { Route } from './route'

const mocks = vi.hoisted(() => ({
  provider: 'opensource',
  uploadProvider: vi.fn(),
  preview: vi.fn(),
  agent: vi.fn(),
  terminal: vi.fn(),
  fetchFsStat: vi.fn(),
  invalidateList: vi.fn(),
  navigate: vi.fn(),
  refreshLabel: 'refresh',
  search: {} as { file?: string; uri?: string },
}))

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    createFileRoute: () => (options: Record<string, unknown>) => ({
      ...options,
      fullPath: '/filesystem',
      options,
      useSearch: () => mocks.search,
    }),
    useNavigate: () => mocks.navigate,
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('#/hooks/use-studio-service', () => ({
  useStudioService: () => ({ provider: mocks.provider, ready: true }),
}))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'test' }),
}))

vi.mock('#/routes/resources/-hooks/viking-fm', () => ({
  useInvalidateVikingFs: () => ({ invalidateList: mocks.invalidateList }),
  useVikingFsList: () => ({ data: { entries: [] }, isFetching: false }),
}))

vi.mock('#/routes/resources/-lib/api', () => ({
  fetchFsStat: mocks.fetchFsStat,
}))

vi.mock('#/routes/resources/-hooks/use-resource-upload', () => ({
  ResourceUploadProvider: ({
    children,
    enabled,
  }: PropsWithChildren<{ enabled: boolean }>) => {
    mocks.uploadProvider(enabled)
    return children
  },
  useResourceUpload: () => ({
    activeTaskCount: 0,
    hasActiveTasks: false,
    isRefreshingTasks: false,
    refreshTasks: vi.fn(),
    tasks: [],
  }),
}))

vi.mock('#/routes/resources/-components/lazy-file-preview', () => ({
  LazyFilePreview: (props: {
    file: { uri: string } | null
    readOnly?: boolean
    defaultReadLimit?: number
  }) => {
    mocks.preview(props)
    return <div data-testid="preview">{props.file?.uri}</div>
  },
}))

vi.mock('./-components/context-explorer', () => ({
  ContextExplorerHeader: ({ onRefresh }: { onRefresh: () => void }) => (
    <button type="button" aria-label={mocks.refreshLabel} onClick={onRefresh} />
  ),
  ContextTree: () => null,
  PanelTab: () => null,
  FilesystemResizeHandle: () => null,
}))

vi.mock('./-components/agent-panel', () => ({ AgentPanel: mocks.agent }))
vi.mock('./-components/terminal-panel', () => ({
  TerminalPanel: mocks.terminal,
}))
vi.mock('#/routes/resources/-components/find-palette', () => ({
  FindPalette: () => null,
}))

const FilesystemRoute = Route.options.component as ComponentType & {
  preload?: () => Promise<void>
}
const firstFile = 'viking://user/default/memories/first.md'
const secondFile = 'viking://user/default/memories/second.md'
const parentDirectory = 'viking://user/default/memories/'

describe('filesystem context tree refresh', () => {
  beforeAll(async () => {
    await FilesystemRoute.preload?.()
  })

  afterEach(cleanup)

  beforeEach(() => {
    mocks.provider = 'opensource'
    mocks.uploadProvider.mockClear()
    mocks.preview.mockClear()
    mocks.agent.mockReset().mockReturnValue(null)
    mocks.terminal.mockReset().mockReturnValue(null)
    mocks.search = { file: firstFile, uri: parentDirectory }
    mocks.fetchFsStat.mockReset()
    mocks.invalidateList.mockReset().mockResolvedValue(undefined)
    mocks.navigate.mockReset()
  })

  it('reuses the same workbench for hosted reads without scope tabs or incompatible local tools', () => {
    mocks.provider = 'volcengine'
    render(<FilesystemRoute />)
    expect(screen.getByTestId('preview').textContent).toBe(firstFile)
    expect(mocks.preview.mock.lastCall?.[0].readOnly).not.toBe(true)
    expect(mocks.preview.mock.lastCall?.[0].defaultReadLimit).toBe(10000)
    expect(mocks.uploadProvider.mock.lastCall?.[0]).toBe(false)
    expect(mocks.agent).not.toHaveBeenCalled()
    expect(mocks.terminal).not.toHaveBeenCalled()
    expect(screen.queryByRole('tab')).toBeNull()
    expect(screen.queryByText('addResource.title')).toBeNull()
  })

  it('clears a deleted selected file from the preview and URL search', async () => {
    mocks.fetchFsStat.mockRejectedValue({ statusCode: 404 })
    render(<FilesystemRoute />)

    await userEvent.click(
      await screen.findByRole('button', { name: mocks.refreshLabel }),
    )

    await waitFor(() => {
      expect(screen.getByTestId('preview').textContent).toBe('')
    })
    expect(mocks.invalidateList.mock.calls[0]).toEqual([])
    expect(mocks.fetchFsStat).toHaveBeenCalledWith(firstFile, {
      throwOnError: true,
    })
    const navigation = mocks.navigate.mock.calls[0][0]
    expect(
      navigation.search({ file: firstFile, uri: parentDirectory }),
    ).toEqual({ uri: parentDirectory })
  })

  it('does not clear a newer selection when the old file stat finishes late', async () => {
    let rejectStat: (error: unknown) => void = () => undefined
    mocks.fetchFsStat.mockReturnValue(
      new Promise((_, reject) => {
        rejectStat = reject
      }),
    )
    const { rerender } = render(<FilesystemRoute />)

    await userEvent.click(
      await screen.findByRole('button', { name: mocks.refreshLabel }),
    )
    await waitFor(() => {
      expect(mocks.fetchFsStat).toHaveBeenCalledOnce()
    })

    mocks.search = { file: secondFile, uri: parentDirectory }
    rerender(<FilesystemRoute />)
    await waitFor(() => {
      expect(screen.getByTestId('preview').textContent).toBe(secondFile)
    })

    await act(async () => {
      rejectStat({ statusCode: 404 })
    })

    expect(screen.getByTestId('preview').textContent).toBe(secondFile)
    expect(mocks.navigate).not.toHaveBeenCalled()
  })
})
