// @vitest-environment jsdom
import type { FilePreviewActions } from '@openviking/react/components/file-preview'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ComponentType, PropsWithChildren, ReactNode } from 'react'
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
  copyContent: vi.fn(async () => true),
  agent: vi.fn(),
  terminal: vi.fn(),
  fetchFsStat: vi.fn(),
  invalidateList: vi.fn(),
  invalidateAll: vi.fn(),
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
    useBlocker: () => undefined,
  }
})

vi.mock('#/app/layouts/studio-header-actions', () => ({
  StudioHeaderActions: ({ children }: PropsWithChildren) => children,
}))

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
  useInvalidateVikingFs: () => ({
    invalidateList: mocks.invalidateList,
    invalidateAll: mocks.invalidateAll,
  }),
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

vi.mock(
  '#/features/workbench/filesystem/components/shared-file-preview',
  () => ({
    SharedFilePreview: (props: {
      file: { uri: string } | null
      readOnly?: boolean
      defaultReadLimit?: number
      extraActions?:
        | ReactNode
        | ((
            actions: Pick<
              FilePreviewActions,
              'canCopyContent' | 'busy' | 'copyContent'
            >,
          ) => ReactNode)
    }) => {
      mocks.preview(props)
      return (
        <>
          <div data-testid="preview">{props.file?.uri}</div>
          {typeof props.extraActions === 'function'
            ? props.extraActions({
                canCopyContent: true,
                busy: false,
                copyContent: mocks.copyContent,
              })
            : props.extraActions}
        </>
      )
    },
  }),
)

vi.mock('#/features/workbench/filesystem/components/context-explorer', () => ({
  ContextTree: ({ onRefresh }: { onRefresh: () => void }) => (
    <button type="button" aria-label={mocks.refreshLabel} onClick={onRefresh} />
  ),
  ContextExplorerHeader: () => null,
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
vi.mock(
  '#/features/workbench/filesystem/components/delete-document-dialog',
  () => ({
    DeleteDocumentDialog: ({
      uri,
      onClose,
      onDeleted,
    }: {
      uri: string
      onClose: () => void
      onDeleted: (uri: string) => void
    }) => (
      <div role="alertdialog">
        <span>{uri}</span>
        <button onClick={onClose}>cancel-delete</button>
        <button onClick={() => onDeleted(uri)}>confirm-delete</button>
      </div>
    ),
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

function renderFilesystem() {
  const client = new QueryClient()
  return render(<FilesystemRoute />, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  })
}

describe('filesystem context tree refresh', () => {
  beforeAll(async () => {
    await FilesystemRoute.preload?.()
  })

  afterEach(cleanup)

  beforeEach(() => {
    mocks.provider = 'opensource'
    mocks.uploadProvider.mockClear()
    mocks.preview.mockClear()
    mocks.copyContent.mockClear()
    mocks.agent.mockReset().mockReturnValue(null)
    mocks.terminal.mockReset().mockReturnValue(null)
    mocks.search = { file: firstFile, uri: parentDirectory }
    mocks.fetchFsStat.mockReset()
    mocks.invalidateList.mockReset().mockResolvedValue(undefined)
    mocks.invalidateAll.mockReset().mockResolvedValue(undefined)
    mocks.navigate.mockReset()
    mocks.dataManagement.mockReset()
  })

  it('copies the preview content from the more menu', async () => {
    renderFilesystem()
    await userEvent.click(screen.getByLabelText('ide.more'))
    await userEvent.click(
      screen.getByRole('button', { name: 'dataManagement:copyContent' }),
    )
    expect(mocks.copyContent).toHaveBeenCalledOnce()
    expect(screen.getByLabelText('ide.more').closest('details')?.open).toBe(
      false,
    )
  })

  it('offers file deletion and clears the deleted preview and URL', async () => {
    renderFilesystem()
    await userEvent.click(screen.getByLabelText('ide.more'))
    await userEvent.click(
      screen.getByRole('button', { name: 'dataManagement:deleteDocument' }),
    )
    expect(screen.getByRole('alertdialog').textContent).toContain(firstFile)
    expect(screen.getByTestId('preview')).toBeTruthy()
    await userEvent.click(
      screen.getByRole('button', { name: 'confirm-delete' }),
    )
    await waitFor(() => expect(screen.queryByTestId('preview')).toBeNull())
    expect(mocks.invalidateAll).toHaveBeenCalled()
    const navigation = mocks.navigate.mock.lastCall![0]
    expect(
      navigation.search({ file: firstFile, uri: parentDirectory }),
    ).toEqual({ uri: parentDirectory })
  })
  it('preserves a newer selection after deletion of the confirmed file', async () => {
    const { rerender } = renderFilesystem()
    await userEvent.click(screen.getByLabelText('ide.more'))
    await userEvent.click(
      screen.getByRole('button', { name: 'dataManagement:deleteDocument' }),
    )
    mocks.search = { file: secondFile, uri: parentDirectory }
    rerender(<FilesystemRoute />)
    await waitFor(() =>
      expect(screen.getByTestId('preview').textContent).toBe(secondFile),
    )
    await userEvent.click(
      screen.getByRole('button', { name: 'confirm-delete' }),
    )
    expect(screen.getByTestId('preview').textContent).toBe(secondFile)
  })

  it('opens the standalone task list from the workbench header', async () => {
    renderFilesystem()

    await userEvent.click(screen.getByRole('button', { name: 'title' }))

    expect(mocks.navigate).toHaveBeenCalledWith({
      to: '/tasks',
      search: { file: firstFile, uri: parentDirectory },
    })
  })

  it('offers OVPack for the parent resource directory while a file is selected', async () => {
    mocks.search = {
      file: 'viking://resources/preferences/response-tone.md',
      uri: 'viking://resources/preferences/',
    }
    renderFilesystem()

    await userEvent.click(screen.getByLabelText('ide.more'))
    await userEvent.click(
      screen.getByRole('button', { name: 'dataManagement:exportMenu' }),
    )
    expect(screen.getByTestId('data-management-uri').textContent).toBe(
      'viking://resources/preferences/',
    )
    expect(mocks.dataManagement.mock.lastCall?.[0].isDirectory).toBe(true)
    expect(mocks.dataManagement.mock.lastCall?.[0].initialTransferTab).toBe(
      'export',
    )
  })

  it('offers OVPack on hosted resource directories too', async () => {
    mocks.provider = 'volcengine'
    mocks.search = {
      file: 'viking://resources/preferences/response-tone.md',
      uri: 'viking://resources/preferences/',
    }
    renderFilesystem()

    await userEvent.click(screen.getByLabelText('ide.more'))
    await userEvent.click(
      screen.getByRole('button', { name: 'dataManagement:importMenu' }),
    )
    expect(screen.getByTestId('data-management-uri').textContent).toBe(
      'viking://resources/preferences/',
    )
    expect(mocks.dataManagement.mock.lastCall?.[0].isDirectory).toBe(true)
    expect(mocks.dataManagement.mock.lastCall?.[0].initialTransferTab).toBe(
      'import',
    )
  })

  it('shows only the CLI tab for hosted reads while keeping local tools unavailable', async () => {
    mocks.provider = 'volcengine'
    mocks.search = { file: firstFile, panel: 'agent', uri: parentDirectory }
    renderFilesystem()
    await userEvent.click(screen.getByLabelText('ide.more'))
    await userEvent.click(
      screen.getByRole('button', { name: 'dataManagement:historyMenu' }),
    )
    expect(screen.getByTestId('data-management-uri').textContent).toBe(
      firstFile,
    )
    expect(
      screen.queryByRole('button', { name: 'dataManagement:exportMenu' }),
    ).toBeNull()
    expect(screen.getByTestId('preview').textContent).toBe(firstFile)
    expect(mocks.preview.mock.lastCall?.[0].readOnly).not.toBe(true)
    expect(mocks.preview.mock.lastCall?.[0].defaultReadLimit).toBe(10000)
    expect(mocks.uploadProvider.mock.lastCall?.[0]).toBe(false)
    expect(mocks.agent).not.toHaveBeenCalled()
    expect(mocks.terminal).toHaveBeenCalled()
    expect(mocks.agent).not.toHaveBeenCalled()
    expect(
      screen.getAllByRole('button', { name: 'tabs.terminal' }),
    ).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'tabs.terminal' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'tabs.agent' })).toBeNull()
    expect(screen.queryByText('addResource.title')).toBeNull()
  })

  it('clears a deleted selected file from the preview and URL search', async () => {
    mocks.fetchFsStat.mockRejectedValue({ statusCode: 404 })
    renderFilesystem()

    await userEvent.click(
      await screen.findByRole('button', { name: mocks.refreshLabel }),
    )

    await waitFor(() => {
      expect(screen.queryByTestId('preview')).toBeNull()
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
    const { rerender } = renderFilesystem()

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
