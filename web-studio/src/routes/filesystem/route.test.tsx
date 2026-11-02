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

import { Route } from '#/routes/filesystem/route'

const mocks = vi.hoisted(() => ({
  provider: 'opensource',
  uploadProvider: vi.fn(),
  preview: vi.fn(),
  agent: vi.fn(),
  terminal: vi.fn(),
  fetchFsStat: vi.fn(),
  invalidateList: vi.fn(),
  navigate: vi.fn(),
  dataManagement: vi.fn(),
  refreshLabel: 'refresh',
  search: {} as { file?: string; panel?: 'agent' | 'terminal'; uri?: string },
}))

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    getRouteApi: () => ({ useSearch: () => mocks.search }),
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

vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({ provider: mocks.provider, ready: true }),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'test' }),
}))

vi.mock('#/features/workbench/resources/hooks/viking-fm', () => ({
  useInvalidateVikingFs: () => ({ invalidateList: mocks.invalidateList }),
  useVikingFsList: () => ({ data: { entries: [] }, isFetching: false }),
}))

vi.mock('#/features/workbench/resources/lib/api', () => ({
  fetchFsStat: mocks.fetchFsStat,
}))

vi.mock('#/features/workbench/resources/hooks/use-resource-upload', () => ({
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

vi.mock('#/features/workbench/resources/components/lazy-file-preview', () => ({
  LazyFilePreview: (props: {
    file: { uri: string } | null
    readOnly?: boolean
    defaultReadLimit?: number
  }) => {
    mocks.preview(props)
    return <div data-testid="preview">{props.file?.uri}</div>
  },
}))

vi.mock('#/features/workbench/filesystem/components/context-explorer', () => ({
  ContextExplorerHeader: ({ onRefresh }: { onRefresh: () => void }) => (
    <button type="button" aria-label={mocks.refreshLabel} onClick={onRefresh} />
  ),
  ContextTree: () => null,
  PanelTab: ({ label }: { label: string }) => (
    <button type="button" role="tab">
      {label}
    </button>
  ),
  FilesystemResizeHandle: () => null,
}))

vi.mock('#/features/workbench/filesystem/components/agent-panel', () => ({
  AgentPanel: mocks.agent,
}))
vi.mock('#/features/workbench/filesystem/components/terminal-panel', () => ({
  TerminalPanel: mocks.terminal,
}))
vi.mock(
  '#/features/workbench/filesystem/components/data-management-dialog',
  () => ({
    DataManagementDialog: (props: { uri: string; isDirectory: boolean }) => {
      mocks.dataManagement(props)
      return <div data-testid="data-management-uri">{props.uri}</div>
    },
  }),
)
vi.mock('#/features/workbench/resources/components/find-palette', () => ({
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
    mocks.dataManagement.mockReset()
  })

  it('offers OVPack for the parent resource directory while a file is selected', async () => {
    mocks.search = {
      file: 'viking://resources/preferences/response-tone.md',
      uri: 'viking://resources/preferences/',
    }
    render(<FilesystemRoute />)

    await userEvent.click(
      screen.getByRole('button', { name: 'dataManagement:transferTitle' }),
    )
    expect(screen.getByTestId('data-management-uri').textContent).toBe(
      'viking://resources/preferences/',
    )
    expect(mocks.dataManagement.mock.lastCall?.[0].isDirectory).toBe(true)
  })

  it('offers OVPack on hosted resource directories too', async () => {
    mocks.provider = 'volcengine'
    mocks.search = {
      file: 'viking://resources/preferences/response-tone.md',
      uri: 'viking://resources/preferences/',
    }
    render(<FilesystemRoute />)

    await userEvent.click(
      screen.getByRole('button', { name: 'dataManagement:transferTitle' }),
    )
    expect(screen.getByTestId('data-management-uri').textContent).toBe(
      'viking://resources/preferences/',
    )
    expect(mocks.dataManagement.mock.lastCall?.[0].isDirectory).toBe(true)
  })

  it('shows only the CLI tab for hosted reads while keeping local tools unavailable', async () => {
    mocks.provider = 'volcengine'
    mocks.search = { file: firstFile, panel: 'agent', uri: parentDirectory }
    render(<FilesystemRoute />)
    await userEvent.click(
      screen.getByRole('button', { name: 'dataManagement:historyTitle' }),
    )
    expect(screen.getByTestId('data-management-uri').textContent).toBe(
      firstFile,
    )
    expect(
      screen.queryByRole('button', { name: 'dataManagement:transferTitle' }),
    ).toBeNull()
    expect(screen.getByTestId('preview').textContent).toBe(firstFile)
    expect(mocks.preview.mock.lastCall?.[0].readOnly).not.toBe(true)
    expect(mocks.preview.mock.lastCall?.[0].defaultReadLimit).toBe(10000)
    expect(mocks.uploadProvider.mock.lastCall?.[0]).toBe(false)
    expect(mocks.agent).not.toHaveBeenCalled()
    expect(mocks.terminal).toHaveBeenCalled()
    expect(mocks.agent).not.toHaveBeenCalled()
    expect(screen.getAllByRole('tab')).toHaveLength(1)
    expect(screen.getByRole('tab', { name: 'tabs.terminal' })).toBeTruthy()
    expect(screen.queryByRole('tab', { name: 'tabs.agent' })).toBeNull()
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
