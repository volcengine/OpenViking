// @vitest-environment jsdom
import { FilePreview } from '@openviking/react/components/file-preview'
import type { FileBrowserProps } from '@openviking/react/modules/file-browser'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ComponentType, PropsWithChildren } from 'react'
import {
  afterAll,
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
  editor: vi.fn(),
  agent: vi.fn(),
  console: vi.fn(),
  fetchFsStat: vi.fn(),
  invalidateList: vi.fn(),
  invalidateAll: vi.fn(),
  navigate: vi.fn(),
  dataManagement: vi.fn(),
  search: {} as { file?: string; panel?: 'agent' | 'terminal'; uri?: string },
}))

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal()
  return Object.assign({}, actual, {
    getRouteApi: () => ({ useSearch: () => mocks.search }),
    createFileRoute: () => (options: Record<string, unknown>) => ({
      ...options,
      fullPath: '/filesystem',
      options,
      useSearch: () => mocks.search,
    }),
    useNavigate: () => mocks.navigate,
    useBlocker: () => undefined,
  })
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
  '#/features/workbench/filesystem/components/workbench-file-browser',
  () => ({
    WorkbenchFileBrowser: (props: FileBrowserProps) => {
      mocks.preview(props)
      return (
        <>
          <div data-testid="preview">{props.uri}</div>
          <FilePreview
            locale="en-US"
            tabs={[
              {
                kind: 'file',
                key: props.uri ?? 'empty',
                source: { path: props.uri, fileName: 'preview.md', content: '' },
                actions: (context) => {
                  const actionItems = props.previewSlots?.actionItems
                  return typeof actionItems === 'function'
                    ? actionItems({
                        ...context,
                        canDelete: false,
                        deleteFile: vi.fn(),
                        defaultActions: [
                          {
                            key: 'download',
                            label: 'Download',
                            icon: null,
                            onClick: vi.fn(),
                          },
                        ],
                      })
                    : (actionItems ?? [])
                },
              },
            ]}
          />
        </>
      )
    },
  }),
)

vi.mock(
  '#/features/workbench/filesystem/components/shared-file-preview',
  () => ({
    SharedFilePreview: (props: { onDirtyChange: (dirty: boolean) => void }) => {
      mocks.editor(props)
      return null
    },
  }),
)

vi.mock(
  '#/features/workbench/filesystem/components/filesystem-resize-handle',
  () => ({
    FilesystemResizeHandle: () => null,
  }),
)

vi.mock('#/features/workbench/filesystem/components/agent-panel', () => ({
  AgentPanel: mocks.agent,
}))
vi.mock('#/features/workbench/filesystem/components/workbench-console', () => ({
  WorkbenchConsole: mocks.console,
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

async function openPreviewActions() {
  const trigger = screen.getByRole('button', { name: 'More actions' })
  trigger.focus()
  await userEvent.keyboard('{ArrowDown}')
}

describe('filesystem workbench', () => {
  beforeAll(async () => {
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        disconnect() {}
      },
    )
    await FilesystemRoute.preload?.()
  })

  afterAll(() => vi.unstubAllGlobals())
  afterEach(cleanup)

  beforeEach(() => {
    mocks.provider = 'opensource'
    mocks.uploadProvider.mockClear()
    mocks.preview.mockClear()
    mocks.agent.mockReset().mockReturnValue(null)
    mocks.console.mockReset().mockReturnValue(null)
    mocks.search = { file: firstFile, uri: parentDirectory }
    mocks.fetchFsStat.mockReset()
    mocks.invalidateList.mockReset().mockResolvedValue(undefined)
    mocks.invalidateAll.mockReset().mockResolvedValue(undefined)
    mocks.navigate.mockReset()
    mocks.dataManagement.mockReset()
  })

  it('uses only native tabs for a direct user experience file', () => {
    const uri = 'viking://user/alice/memories/experiences/release-check.md'
    mocks.search = { file: uri }
    renderFilesystem()

    const props: FileBrowserProps = mocks.preview.mock.lastCall![0]
    expect(props.uri).toBe(uri)
    expect(screen.queryByText('ide.experienceDetails')).toBeNull()
  })

  it('keeps the current resource when the user declines to discard an edit', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    try {
      mocks.fetchFsStat.mockResolvedValue({ isDir: false, name: 'other.md' })
      renderFilesystem()
      await openPreviewActions()
      await userEvent.click(
        screen.getByRole('menuitem', { name: 'resources:filePreview.edit' }),
      )

      await act(async () => {
        const props: FileBrowserProps = mocks.preview.mock.lastCall![0]
        mocks.editor.mock.lastCall![0].onDirtyChange(true)
        props.onUriChange?.(secondFile)
      })

      expect(confirm).toHaveBeenCalledOnce()
      expect(mocks.navigate).not.toHaveBeenCalled()
      expect(screen.getByTestId('preview').textContent).toBe(firstFile)
    } finally {
      confirm.mockRestore()
    }
  })

  it('offers file deletion and clears the deleted preview and URL', async () => {
    renderFilesystem()
    await openPreviewActions()
    expect(screen.getByRole('menuitem', { name: 'Download' })).toBeTruthy()
    await userEvent.click(
      screen.getByRole('menuitem', { name: 'dataManagement:deleteDocument' }),
    )
    expect(screen.getByRole('alertdialog').textContent).toContain(firstFile)
    expect(screen.getByTestId('preview')).toBeTruthy()
    await userEvent.click(
      screen.getByRole('button', { name: 'confirm-delete' }),
    )
    await waitFor(() =>
      expect(screen.getByTestId('preview').textContent).toBe(
        parentDirectory.slice(0, -1),
      ),
    )
    expect(mocks.invalidateAll).toHaveBeenCalled()
    const navigation = mocks.navigate.mock.lastCall![0]
    expect(
      navigation.search({ file: firstFile, uri: parentDirectory }),
    ).toEqual({ uri: parentDirectory })
  })
  it('preserves a newer selection after deletion of the confirmed file', async () => {
    const { rerender } = renderFilesystem()
    await openPreviewActions()
    await userEvent.click(
      screen.getByRole('menuitem', { name: 'dataManagement:deleteDocument' }),
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

    await openPreviewActions()
    await userEvent.click(
      screen.getByRole('menuitem', { name: 'dataManagement:exportMenu' }),
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

    await openPreviewActions()
    await userEvent.click(
      screen.getByRole('menuitem', { name: 'dataManagement:importMenu' }),
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
    await openPreviewActions()
    await userEvent.click(
      screen.getByRole('menuitem', { name: 'dataManagement:historyMenu' }),
    )
    expect(screen.getByTestId('data-management-uri').textContent).toBe(
      firstFile,
    )
    expect(
      screen.queryByRole('menuitem', { name: 'dataManagement:exportMenu' }),
    ).toBeNull()
    expect(screen.getByTestId('preview').textContent).toBe(firstFile)
    expect(mocks.preview.mock.lastCall?.[0].readOnly).toBe(true)
    expect(mocks.uploadProvider.mock.lastCall?.[0]).toBe(false)
    expect(mocks.agent).not.toHaveBeenCalled()
    expect(mocks.console.mock.lastCall?.[0]).toMatchObject({ mode: 'command' })
    expect(mocks.console.mock.lastCall?.[0].conversation).toBeUndefined()
    expect(screen.queryByText('addResource.title')).toBeNull()
  })
})
