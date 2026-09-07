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
      fullPath: '/playground',
      options,
      useSearch: () => mocks.search,
    }),
    useNavigate: () => mocks.navigate,
  }
})

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
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
  ResourceUploadProvider: ({ children }: PropsWithChildren) => children,
  useResourceUpload: () => ({
    activeTaskCount: 0,
    hasActiveTasks: false,
    isRefreshingTasks: false,
    refreshTasks: vi.fn(),
    tasks: [],
  }),
}))

vi.mock('#/routes/resources/-components/lazy-file-preview', () => ({
  LazyFilePreview: ({ file }: { file: { uri: string } | null }) => (
    <div data-testid="preview">{file?.uri}</div>
  ),
}))

vi.mock('./-components/context-explorer', () => ({
  ContextExplorerHeader: ({ onRefresh }: { onRefresh: () => void }) => (
    <button type="button" aria-label={mocks.refreshLabel} onClick={onRefresh} />
  ),
  ContextTree: () => null,
  PanelTab: () => null,
  PlaygroundResizeHandle: () => null,
}))

vi.mock('./-components/agent-panel', () => ({ AgentPanel: () => null }))
vi.mock('./-components/terminal-panel', () => ({ TerminalPanel: () => null }))
vi.mock('#/routes/resources/-components/find-palette', () => ({
  FindPalette: () => null,
}))

const PlaygroundRoute = Route.options.component as ComponentType & {
  preload?: () => Promise<void>
}
const firstFile = 'viking://user/default/memories/first.md'
const secondFile = 'viking://user/default/memories/second.md'
const parentDirectory = 'viking://user/default/memories/'

describe('playground context tree refresh', () => {
  beforeAll(async () => {
    await PlaygroundRoute.preload?.()
  })

  afterEach(cleanup)

  beforeEach(() => {
    mocks.search = { file: firstFile, uri: parentDirectory }
    mocks.fetchFsStat.mockReset()
    mocks.invalidateList.mockReset().mockResolvedValue(undefined)
    mocks.navigate.mockReset()
  })

  it('clears a deleted selected file from the preview and URL search', async () => {
    mocks.fetchFsStat.mockRejectedValue({ statusCode: 404 })
    render(<PlaygroundRoute />)

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
    const { rerender } = render(<PlaygroundRoute />)

    await userEvent.click(
      await screen.findByRole('button', { name: mocks.refreshLabel }),
    )
    await waitFor(() => {
      expect(mocks.fetchFsStat).toHaveBeenCalledOnce()
    })

    mocks.search = { file: secondFile, uri: parentDirectory }
    rerender(<PlaygroundRoute />)
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
